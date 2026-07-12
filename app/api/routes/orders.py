from typing import List
from uuid import UUID, uuid4

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class OrderItemRequest(BaseModel):
    book_id: UUID
    quantity: int


class CreateOrderRequest(BaseModel):
    customer_name: str
    items: List[OrderItemRequest]


class CreateOrderResponse(BaseModel):
    order_id: UUID
    total_price: int
    applied_discount: str


@router.post("", response_model=CreateOrderResponse)
def create_order(request: CreateOrderRequest):
    return CreateOrderResponse(
        order_id=uuid4(),
        total_price=15000,
        applied_discount="UBCI S급 적용",
    )
