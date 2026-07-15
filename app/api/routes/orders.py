from decimal import Decimal
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.core.database import get_session
from app.models.wms import Book, Order, OrderItem, OrderStatus, OrderType

router = APIRouter()


class OrderItemRequest(BaseModel):
    book_id: UUID
    quantity: int = Field(gt=0)


class CreateOrderRequest(BaseModel):
    customer_name: str = Field(min_length=1)
    items: List[OrderItemRequest] = Field(min_length=1)


class CreateOrderResponse(BaseModel):
    order_id: UUID
    total_price: Decimal
    applied_discount: str  # 할인 적용되었는지 상태를 나타내는 str 값. 
    status: OrderStatus


@router.post("", response_model=CreateOrderResponse)
def create_order(
    request: CreateOrderRequest,
    session: Session = Depends(get_session),
):
    requested_items: dict[UUID, int] = {}
    for item in request.items:
        requested_items[item.book_id] = requested_items.get(item.book_id, 0) + item.quantity

    requested_book_ids = set(requested_items)
    books = session.exec(select(Book).where(Book.id.in_(requested_book_ids))).all()
    books_by_id = {book.id: book for book in books}
    missing_book_ids = requested_book_ids - set(books_by_id)

    if missing_book_ids:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "message": "One or more requested books were not found",
                "missing_book_ids": [str(book_id) for book_id in missing_book_ids],
            },
        )

    unpriced_books = [
        book_id for book_id, book in books_by_id.items() if book.base_price <= 0
    ]
    if unpriced_books:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "One or more requested books do not have a valid base price",
                "unpriced_book_ids": [str(book_id) for book_id in unpriced_books],
            },
        )

    total_price = sum(
        books_by_id[book_id].base_price * quantity
        for book_id, quantity in requested_items.items()
    )

    order = Order(
        customer_name=request.customer_name,
        type=OrderType.B2B_ORDER,
        total_price=total_price,
        status=OrderStatus.PENDING,
    )
    session.add(order)
    session.flush()

    for book_id, quantity in requested_items.items():
        book = books_by_id[book_id]
        session.add(
            OrderItem(
                order_id=order.id,
                book_id=book.id,
                quantity=quantity,
                unit_price=book.base_price,
                final_price=book.base_price * quantity,
            )
        )

    session.commit()
    session.refresh(order)

    return CreateOrderResponse(
        order_id=order.id,
        total_price=order.total_price,
        applied_discount="NO_DISCOUNT_APPLIED",
        status=order.status,
    )
