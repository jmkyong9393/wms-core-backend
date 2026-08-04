from datetime import datetime
from uuid import UUID
from decimal import Decimal

from pydantic import BaseModel, Field

from app.models.wms import OrderStatus, OrderType

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


class OutboundDashboardOrderResponse(BaseModel):
    id: UUID
    customer_name: str | None = None
    order_type: OrderType
    total_price: Decimal
    status: OrderStatus
    waybill_number: str | None = None
    created_at: datetime
    shipped_at: datetime | None = None


class OutboundDashboardSummaryResponse(BaseModel):
    active_picking_order_count: int = Field(
        description="현재 PICKING 상태인 B2B 주문 수"
    )
    picking_completion_rate: float = Field(
        ge=0,
        le=100,
        description="진행 중 피킹 주문의 예약 수량 대비 스캔 완료 비율"
    )
    today_shipping_label_issued_count: int = Field(
        description="오늘 출고 확정되어 송장이 발급된 B2B 주문 수"
    )
    recent_orders: list[OutboundDashboardOrderResponse] = Field(
        description="최근 생성된 B2B 출고 주문 목록"
    )
