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


class OrderProposal(SQLModel, table=True):
    __tablename__ = "order_proposals"
    __table_args__ = (
        UniqueConstraint("return_job_id", name="uq_order_proposals_return_job",),
        CheckConstraint("pending_auto_po_quantity >= 0", name="ck_order_proposals_pending_auto_po_nonnegative",),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True,)
    tenant_id: uuid.UUID = Field(foreign_key="tenants.id",nullable=False,index=True,)
    book_id: uuid.UUID = Field(foreign_key="books.id",nullable=False,index=True,)
    return_job_id: uuid.UUID | None = Field(default=None,foreign_key="return_jobs.id",index=True,)
    proposal_source: RestockProposalSource = Field(default=RestockProposalSource.RETURN_REJECTION,nullable=False,index=True,)
    recent_sales_quantity: int = Field(nullable=False)
    current_stock: int = Field(nullable=False)

    pending_auto_po_quantity: int = Field(default=0,nullable=False,)
    rejected_quantity: int = Field(nullable=False)
    rejection_reason_code: str = Field(nullable=False)

    recommended_order_quantity: int = Field(nullable=False)
    reason_summary: str = Field(nullable=False)
    evidence: list = Field(default_factory=list,sa_column=Column(JSONB, nullable=False),)
    risk_level: str = Field(nullable=False)

    status: OrderProposalStatus = Field(default=OrderProposalStatus.PENDING,nullable=False,index=True,)

    auto_po_order_id: uuid.UUID | None = Field(default=None,foreign_key="orders.id",index=True,)
    reviewer_id: uuid.UUID | None = Field(default=None,foreign_key="users.id",index=True,)
    reviewed_at: datetime | None = Field(default=None)
    review_comment: str | None = Field(default=None,max_length=1000,)
    created_at: datetime = Field(default_factory=datetime.utcnow,)
    updated_at: datetime = Field(default_factory=datetime.utcnow,)
