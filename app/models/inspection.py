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


class ReturnJob(SQLModel, table=True):
    __tablename__ = "return_jobs"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    tenant_id: UUID = Field(foreign_key="tenants.id", nullable=False, index=True,)

    order_id: Optional[uuid.UUID] = Field(default=None, foreign_key="orders.id")
    book_id: uuid.UUID = Field(foreign_key="books.id")
    inbound_item_id: Optional[uuid.UUID] = Field(
        default=None,
        foreign_key="inbound_items.id",
    )

    task_id: Optional[str] = Field(default=None)

    mode: InspectionMode = Field(nullable=False)

    status: ReturnJobStatus = Field(default=ReturnJobStatus.PENDING)

    image_paths: Optional[list] = Field(default=None, sa_column=Column(JSONB))

    ubci_score: Optional[Decimal] = Field(
        default=None,
        sa_column=Column(Numeric(5, 2)),
    )
    condition_grade: Optional[ConditionGrade] = Field(default=None)

    agent_logs: Optional[dict] = Field(
        default_factory=dict,
        sa_column=Column(JSONB),
    )

    final_report: Optional[str] = Field(default=None)

    ai_inspection_started_at: datetime = Field(
        default_factory=datetime.utcnow,
        nullable=False,
    )
    ai_inspection_completed_at: Optional[datetime] = Field(
        default=None,
    )

    hitl_reviewer_id: UUID | None = Field(default=None,foreign_key="users.id",index=True,)
    hitl_review_started_at: datetime | None = Field(default=None,)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
