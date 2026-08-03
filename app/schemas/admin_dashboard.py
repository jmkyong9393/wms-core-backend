from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, Field

class WeeklyInsightResponse(BaseModel):
    id: UUID
    report_week: str
    saved_labor_cost_krw: int
    top_defective_publishers: dict[str, int] | None = None
    location_hotspots: dict[str, int] | None = None
    logistics_hotspots: dict[str, int] | None = None
    predicted_returns: int
    created_at: datetime
    updated_at: datetime

class FdsReportResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    customer_id: UUID
    customer_name: str | None = None
    fraud_score: int
    fraud_reason: str | None = None
    detected_at: datetime
    created_at: datetime
    updated_at: datetime

class FdsPolicyResponse(BaseModel):
    policy_key: str
    policy_value: float
    description: str | None = None
    updated_at: datetime

class FdsPolicyUpdateRequest(BaseModel):
    policy_value: float = Field(ge=0)
