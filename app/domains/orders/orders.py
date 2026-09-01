from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, select
from sqlalchemy import func

from app.core.database import get_session
from app.models.wms import (
    Book,
    ConditionGrade,
    Order,
    OrderItem,
    OrderStatus,
    OrderType,
    User,
)

from app.api.dependencies.auth import require_wms_operator

router = APIRouter()


class OrderItemRequest(BaseModel):
    book_id: UUID = Field(description="주문할 도서 마스터 ID")
    condition_grade: Optional[ConditionGrade] = Field(
        default=None,
        description=(
            "중고 단품 주문 등급. 값이 없으면 신간 묶음 재고 주문으로 처리"
        ),
    )
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
                        "condition_grade": "EXCELLENT",
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
        description="신간 묶음 또는 등급별 중고 단품 주문 품목 목록",
    )


class CreateOrderResponse(BaseModel):
    order_id: UUID = Field(description="생성된 주문 ID")
    total_price: Decimal = Field(description="주문 생성 시점의 총 주문 금액")
    applied_discount: str = Field(description="적용된 할인 정책 식별자")
    status: OrderStatus = Field(description="주문 처리 상태")

class OrderListItemResponse(BaseModel):
    id: UUID
    customer_name: str
    status: OrderStatus
    total_price: Decimal
    logistics_center: str | None = None
    created_at: datetime

class OrderListResponse(BaseModel):
    items: list[OrderListItemResponse]
    total: int
    page: int
    size: int
    total_pages: int


@router.get(
    "",
    response_model=OrderListResponse,
    operation_id="listOutboundOrders",
    summary="출고 대상 주문 목록 조회",
    description=(
        "출고 담당자가 피킹할 B2B 주문 목록을 조회합니다. "
        "기본적으로 PENDING 주문을 조회하며, status를 지정하면 "
        "PICKING 또는 SHIPPED 주문도 조회할 수 있습니다."
    ),
)
def list_outbound_orders(
    status_filter: OrderStatus = Query(
        default=OrderStatus.PENDING,
        alias="status",
        description="주문 상태 필터",
    ),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    session: Session = Depends(get_session),
    _: User = Depends(require_wms_operator),
) -> OrderListResponse:
    base_statement = select(Order).where(
        Order.type == OrderType.B2B_ORDER,
        Order.status == status_filter,
    )

    total = session.exec(
        select(func.count())
        .select_from(Order)
        .where(
            Order.type == OrderType.B2B_ORDER,
            Order.status == status_filter,
        )
    ).one()

    orders = session.exec(
        base_statement
        .order_by(Order.created_at.desc(), Order.id.desc())
        .offset((page - 1) * size)
        .limit(size)
    ).all()

    return OrderListResponse(
        items=[
            OrderListItemResponse(
                id=order.id,
                customer_name=order.customer_name or "고객사 미지정",
                status=order.status,
                total_price=order.total_price,
                logistics_center=order.logistics_center,
                created_at=order.created_at,
            )
            for order in orders
        ],
        total=total,
        page=page,
        size=size,
        total_pages=(total + size - 1) // size if total else 0,
    )

@router.post(
    "",
    response_model=CreateOrderResponse,
    operation_id="createWmsOrder",
    summary="신간 및 등급별 중고 B2B 주문 생성",
    description=(
        "book_id, 수량과 선택적 condition_grade를 기준으로 주문을 생성합니다. "
        "등급이 없으면 신간 묶음 재고, 등급이 있으면 해당 등급의 중고 단품 "
        "재고 주문입니다. 신간은 수량을 묶어서 저장하고, 중고는 물리적 책 "
        "한 권당 quantity=1인 주문 품목으로 분리합니다. LPN 선택은 출고 "
        "피킹 시 수행하며, 현재 가격은 도서 기준가를 사용하고 UBCI 동적 "
        "가격은 적용하지 않습니다."
    ),
    responses={
        404: {"description": "주문 품목의 도서 마스터를 찾을 수 없음"},
        409: {"description": "주문 품목에 유효한 기준 가격이 없음"},
        422: {"description": "REJECT 등급 또는 유효하지 않은 요청"},
    },
)
def create_order(
    request: CreateOrderRequest,
    session: Session = Depends(get_session),
):
    rejected_book_ids = [
        item.book_id
        for item in request.items
        if item.condition_grade == ConditionGrade.REJECT
    ]
    if rejected_book_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "REJECT grade inventory cannot be ordered",
                "book_ids": [str(book_id) for book_id in rejected_book_ids],
            },
        )

    new_stock_quantities: dict[UUID, int] = {}
    for item in request.items:
        if item.condition_grade is None:
            new_stock_quantities[item.book_id] = (
                new_stock_quantities.get(item.book_id, 0) + item.quantity
            )

    requested_book_ids = {item.book_id for item in request.items}
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
        books_by_id[item.book_id].base_price * item.quantity
        for item in request.items
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

    for book_id, quantity in new_stock_quantities.items():
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

    for item in request.items:
        if item.condition_grade is None:
            continue

        book = books_by_id[item.book_id]
        for _ in range(item.quantity):
            session.add(
                OrderItem(
                    order_id=order.id,
                    book_id=book.id,
                    condition_grade=item.condition_grade,
                    quantity=1,
                    unit_price=book.base_price,
                    final_price=book.base_price,
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
