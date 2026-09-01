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


class InboundJob(SQLModel, table=True):
    __tablename__ = "inbound_jobs"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    inbound_type: InboundType = Field(nullable=False)
    status: InboundStatus = Field(nullable=False)
    supplier_name: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class InboundItem(SQLModel, table=True):
    __tablename__ = "inbound_items"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    inbound_job_id: uuid.UUID = Field(foreign_key="inbound_jobs.id")
    book_id: uuid.UUID = Field(foreign_key="books.id")
    location_id: Optional[uuid.UUID] = Field(
        default=None,
        foreign_key="locations.id",
    )
    quantity: int = Field(nullable=False)
    lpn_barcode: Optional[str] = Field(default=None, unique=True)
    certificate_token: Optional[str] = Field(default=None, unique=True)
    condition_grade: Optional[ConditionGrade] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
