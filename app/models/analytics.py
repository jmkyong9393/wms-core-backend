import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Column, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.models.enums import *


class FdsPolicy(SQLModel, table=True):
    __tablename__ = "fds_policies"

    policy_key: str = Field(max_length=100, primary_key=True)
    policy_value: Decimal = Field(nullable=False)
    description: Optional[str] = Field(default=None, max_length=500)
    updated_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)


class FdsReport(SQLModel, table=True):
    __tablename__ = "fds_reports"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    tenant_id: uuid.UUID = Field(
        foreign_key="tenants.id",
        nullable=False,
        index=True,
    )
    customer_id: uuid.UUID = Field(nullable=False)
    fraud_score: int = Field(nullable=False)
    fraud_reason: Optional[str] = Field(default=None)
    detected_at: datetime = Field(default_factory=datetime.utcnow)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class WeeklyInsight(SQLModel, table=True):
    __tablename__ = "weekly_insights"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "report_week",
            name="uq_weekly_insights_tenant_report_week",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    # 주차 유일성은 테넌트 단위다.
    tenant_id: uuid.UUID = Field(foreign_key="tenants.id", index=True)
    report_week: str = Field(nullable=False)
    saved_labor_cost_krw: int = Field(default=0)
    top_defective_publishers: Optional[dict] = Field(
        default=None,
        sa_column=Column(JSONB),
    )
    location_hotspots: Optional[dict] = Field(
        default=None,
        sa_column=Column(JSONB),
    )
    logistics_hotspots: Optional[dict] = Field(
        default=None,
        sa_column=Column(JSONB),
    )
    predicted_returns: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
