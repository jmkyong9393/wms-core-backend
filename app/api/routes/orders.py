from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, select

from app.core.database import get_session
from app.models.wms import Book, Order, OrderItem, OrderStatus, OrderType

router = APIRouter()


class OrderItemRequest(BaseModel):
    book_id: UUID = Field(description="주문할 도서 마스터 ID")
    quantity: int = Field(gt=0, description="주문 수량")


class CreateOrderRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "customer_name": "교보문고 강남점",
                "customer_id": None,
                "logistics_center": "SEOUL_DC",
                "items": [
                    {
                        "book_id": "00000000-0000-4000-8000-000000000001",
                        "quantity": 10,
                    }
                ],
            }
        }
    )

    customer_name: str = Field(min_length=1, description="B2B 고객사명")
    customer_id: Optional[UUID] = Field(
        default=None,
        description="고객 고유 ID. 현재 B2B MVP에서는 선택 사항",
    )
    logistics_center: Optional[str] = Field(
        default=None,
        description="주문을 처리할 물류 거점 식별자",
    )
    items: List[OrderItemRequest] = Field(
        min_length=1,
        description="정상 신간 주문 품목 목록",
    )


class CreateOrderResponse(BaseModel):
    order_id: UUID = Field(description="생성된 주문 ID")
    total_price: Decimal = Field(description="주문 생성 시점의 총 주문 금액")
    applied_discount: str = Field(description="적용된 할인 정책 식별자")
    status: OrderStatus = Field(description="주문 처리 상태")


@router.post(
    "",
    response_model=CreateOrderResponse,
    operation_id="createNewStockOrder",
    summary="정상 신간 B2B 주문 생성",
    description=(
        "book_id와 수량을 기준으로 정상 신간 주문을 생성합니다. 현재 구현은 "
        "LPN 기반 중고 단품 주문이나 UBCI 동적 가격을 포함하지 않습니다."
    ),
    responses={
        404: {"description": "주문 품목의 도서 마스터를 찾을 수 없음"},
        409: {"description": "주문 품목에 유효한 기준 가격이 없음"},
    },
)
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
        customer_id=request.customer_id,
        customer_name=request.customer_name,
        type=OrderType.B2B_ORDER,
        total_price=total_price,
        status=OrderStatus.PENDING,
        logistics_center=request.logistics_center,
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
