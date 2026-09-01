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


class Book(SQLModel, table=True):
    __tablename__ = "books"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    title: str = Field(nullable=False)
    isbn: Optional[str] = Field(default=None, unique=True)
    publisher: Optional[str] = Field(default=None)
    cover_image_url: Optional[str] = Field(
        default=None,
        max_length=1000,
    )
    category: BookCategory = Field(nullable=False)
    standard_size: Optional[StandardSize] = Field(default=None)
    thickness_mm: Optional[int] = Field(default=None)
    base_price: Decimal = Field(
        default=Decimal("0"),
        sa_column=Column(Numeric(12, 2), nullable=False, default=0),
    )
    virtual_stock: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Location(SQLModel, table=True):
    __tablename__ = "locations"
    __table_args__ = (
        CheckConstraint(
            "zone IN ('A', 'B', 'C')",
            name="ck_locations_zone",
        ),
        CheckConstraint(
            "rack ~ '^[1-9][0-9]*$'",
            name="ck_locations_rack_positive_integer",
        ),
        CheckConstraint(
            "shelf ~ '^([1-9]|10)$'",
            name="ck_locations_shelf_range",
        ),
        CheckConstraint(
            "barcode = zone || '-' || rack || '-' || shelf",
            name="ck_locations_barcode_components",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    zone: str = Field(nullable=False)
    rack: str = Field(nullable=False)
    shelf: str = Field(nullable=False)
    barcode: str = Field(unique=True, nullable=False)
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
