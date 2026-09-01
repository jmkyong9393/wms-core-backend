import uuid
from uuid import UUID
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from sqlalchemy import CheckConstraint, Column, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import Numeric
from sqlmodel import Field, SQLModel

from app.models.enums import *  # noqa: F401,F403


class Order(SQLModel, table=True):
    __tablename__ = "orders"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    customer_id: Optional[uuid.UUID] = Field(default=None)
    customer_name: Optional[str] = Field(default=None)
    type: OrderType = Field(nullable=False)
    total_price: Decimal = Field(sa_column=Column(Numeric(12, 2), nullable=False))
    status: OrderStatus = Field(nullable=False)
    logistics_center: Optional[str] = Field(default=None)

    # 출고 확정 시 자동 발급되는 택배 송장 번호
    waybill_number: Optional[str] = Field(default=None,max_length=100,unique=True,index=True,)

    # 현재는 내부 발급용 기본 택배사 코드,
    # 실제 택배사 API 연동 시 해당 응답값을 저장
    shipping_carrier: Optional[str] = Field(default=None,max_length=50,)

    # 주문이 실제 출고 확정된 시각
    shipped_at: Optional[datetime] = Field(default=None,)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class OrderItem(SQLModel, table=True):
    __tablename__ = "order_items"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    order_id: uuid.UUID = Field(foreign_key="orders.id")
    book_id: uuid.UUID = Field(foreign_key="books.id")
    location_id: Optional[uuid.UUID] = Field(default=None, foreign_key="locations.id")
    condition_grade: Optional[ConditionGrade] = Field(default=None)
    quantity: int = Field(nullable=False)
    unit_price: Decimal = Field(sa_column=Column(Numeric(12, 2), nullable=False))
    final_price: Decimal = Field(sa_column=Column(Numeric(12, 2), nullable=False))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class OrderItemInventoryAllocation(SQLModel, table=True):
    __tablename__ = "order_item_inventory_allocations"

    __table_args__ = (
        UniqueConstraint("order_item_id","inventory_id",name="uq_order_item_inventory_allocation",),
        CheckConstraint("quantity > 0",name="ck_order_item_inventory_allocation_quantity_positive",),
        CheckConstraint("picked_quantity >= 0 AND picked_quantity <= quantity",name=("ck_order_item_inventory_allocation_picked_quantity"),),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4,primary_key=True,)

    # 피킹 대상 주문 품목
    order_item_id: uuid.UUID = Field(foreign_key="order_items.id",nullable=False,index=True,)

    # 실제 예약한 신간 묶음 재고 행
    inventory_id: uuid.UUID = Field(foreign_key="inventory.id",nullable=False,index=True,)

    # 해당 재고 행에서 예약한 수량
    quantity: int = Field(nullable=False)

    # 실제 ISBN 스캔으로 확인된 수량
    picked_quantity: int = Field(default=0, nullable=False)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class OrderItemLpnAllocation(SQLModel, table=True):
    __tablename__ = "order_item_lpn_allocations"
    __table_args__ = (
        UniqueConstraint(
            "order_item_id",
            "inventory_used_item_id",
            name="uq_order_item_lpn_allocation",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    order_item_id: uuid.UUID = Field(
        foreign_key="order_items.id",
        nullable=False,
        index=True,
    )
    inventory_used_item_id: uuid.UUID = Field(
        foreign_key="inventory_used_items.id",
        nullable=False,
    )

    # 예약된 중고 LPN을 실제로 스캔한 시각
    picked_at: datetime | None = Field(default=None)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
