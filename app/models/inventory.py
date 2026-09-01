import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import CheckConstraint, Column, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import Numeric
from sqlmodel import Field, SQLModel

from app.models.enums import *


class Inventory(SQLModel, table=True):
    __tablename__ = "inventory"
    __table_args__ = (
        UniqueConstraint("book_id", "location_id", name="uq_inventory_book_location"),
        CheckConstraint(
            "reserved_quantity >= 0",
            name="ck_inventory_reserved_quantity_non_negative",
        ),
        CheckConstraint(
            "reserved_quantity <= quantity",
            name="ck_inventory_reserved_quantity_not_exceed_quantity",
        ),
        CheckConstraint(
            "discount_rate IS NULL OR (discount_rate >= 0 AND discount_rate < 1)",
            name="ck_inventory_discount_rate",
        ),
        CheckConstraint(
            "sale_price IS NULL OR sale_price > 0",
            name="ck_inventory_sale_price_positive",
        ),
        CheckConstraint(
            "(discount_rate IS NULL) = (sale_price IS NULL)",
            name="ck_inventory_pricing_pair",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    book_id: uuid.UUID = Field(foreign_key="books.id")
    location_id: uuid.UUID = Field(foreign_key="locations.id")
    quantity: int = Field(default=0)
    # 피킹 지시서에 배정되어 다른 주문이 사용할 수 없는 신간 재고 수량
    reserved_quantity: int = Field(
        default=0,
        nullable=False,
    )
    discount_rate: Optional[Decimal] = Field(
        default=None,
        sa_column=Column(Numeric(5, 4)),
    )
    sale_price: Optional[Decimal] = Field(
        default=None,
        sa_column=Column(Numeric(12, 2)),
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class InventoryUsedItem(SQLModel, table=True):
    __tablename__ = "inventory_used_items"
    __table_args__ = (
        CheckConstraint(
            "condition_grade IN ('MINT', 'EXCELLENT', 'NORMAL')",
            name="ck_inventory_used_items_sellable_grade",
        ),
        CheckConstraint(
            "discount_rate IS NULL OR (discount_rate >= 0 AND discount_rate < 1)",
            name="ck_inventory_used_items_discount_rate",
        ),
        CheckConstraint(
            "sale_price IS NULL OR sale_price > 0",
            name="ck_inventory_used_items_sale_price_positive",
        ),
        CheckConstraint(
            "(discount_rate IS NULL) = (sale_price IS NULL)",
            name="ck_inventory_used_items_pricing_pair",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    book_id: uuid.UUID = Field(foreign_key="books.id")
    location_id: uuid.UUID = Field(foreign_key="locations.id")
    return_job_id: Optional[uuid.UUID] = Field(
        default=None,
        foreign_key="return_jobs.id",
        unique=True,
    )
    lpn_barcode: str = Field(nullable=False, unique=True)
    ubci_score: Optional[Decimal] = Field(
        default=None,
        sa_column=Column(Numeric(5, 2)),
    )
    discount_rate: Optional[Decimal] = Field(
        default=None,
        sa_column=Column(Numeric(5, 4)),
    )
    sale_price: Optional[Decimal] = Field(
        default=None,
        sa_column=Column(Numeric(12, 2)),
    )
    condition_grade: ConditionGrade = Field(nullable=False)
    status: UsedInventoryStatus = Field(
        default=UsedInventoryStatus.AVAILABLE,
        nullable=False,
    )
    certificate_url: Optional[str] = Field(default=None)
    stocked_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class RejectedItem(SQLModel, table=True):
    __tablename__ = "rejected_items"
    __table_args__ = (
        Index(
            "ix_rejected_items_status_location",
            "status",
            "location_id",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    inbound_item_id: uuid.UUID = Field(
        foreign_key="inbound_items.id",
        unique=True,
        nullable=False,
    )
    return_job_id: Optional[uuid.UUID] = Field(
        default=None,
        foreign_key="return_jobs.id",
        unique=True,
    )
    book_id: uuid.UUID = Field(foreign_key="books.id", nullable=False)
    location_id: uuid.UUID = Field(foreign_key="locations.id", nullable=False)
    lpn_barcode: str = Field(nullable=False, unique=True)
    ubci_score: Optional[Decimal] = Field(
        default=None,
        sa_column=Column(Numeric(5, 2)),
    )
    rejection_reason: Optional[dict] = Field(
        default=None,
        sa_column=Column(JSONB),
    )
    status: RejectedItemStatus = Field(
        default=RejectedItemStatus.REJECT_HOLD,
        nullable=False,
    )
    rejected_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    discarded_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class InventoryLog(SQLModel, table=True):
    __tablename__ = "inventory_logs"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    transaction_type: InventoryTransactionType = Field(nullable=False)
    book_id: uuid.UUID = Field(foreign_key="books.id")
    condition_grade: Optional[ConditionGrade] = Field(default=None)
    quantity_change: int = Field(nullable=False)
    target_lpn: Optional[str] = Field(default=None)
    picked_location: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
