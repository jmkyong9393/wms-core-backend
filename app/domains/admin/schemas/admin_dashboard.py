from datetime import date as Date
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.wms import (
    ConditionGrade,
    InboundStatus,
    InboundType,
    OrderStatus,
    OrderType,
)


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
    active_picking_order_count: int = Field(description="현재 PICKING 상태인 B2B 주문 수")
    picking_completion_rate: float = Field(
        ge=0, le=100, description="진행 중 피킹 주문의 예약 수량 대비 스캔 완료 비율"
    )
    today_shipping_label_issued_count: int = Field(description="오늘 출고 확정되어 송장이 발급된 B2B 주문 수")
    recent_orders: list[OutboundDashboardOrderResponse] = Field(description="최근 생성된 B2B 출고 주문 목록")


class DashboardFlowTrendItem(BaseModel):
    date: Date = Field(
        description="집계 기준 일자",
    )
    inbound_quantity: int = Field(
        ge=0,
        description="해당 일자의 입고 처리 수량",
    )
    outbound_quantity: int = Field(
        ge=0,
        description="해당 일자의 출고 처리 수량",
    )
    average_inspection_processing_seconds: float = Field(
        ge=0,
        description="해당 일자에 완료된 AI 검수 건의 평균 처리 시간(초)",
    )


class DashboardFlowTrendResponse(BaseModel):
    days: int = Field(
        ge=1,
        description="조회 기간(일)",
    )
    items: list[DashboardFlowTrendItem] = Field(
        description="오래된 날짜 순 일별 입출고·검수 처리 시간 추이",
    )


class InboundDashboardTrendItem(BaseModel):
    date: Date
    new_stock_quantity: int = Field(ge=0)
    used_return_quantity: int = Field(ge=0)


class InboundDashboardGradeItem(BaseModel):
    grade: ConditionGrade
    quantity: int = Field(ge=0)


class InboundDashboardZoneItem(BaseModel):
    zone: str
    new_stock_quantity: int = Field(ge=0)
    used_stock_quantity: int = Field(ge=0)
    available_quantity: int = Field(ge=0)


class RecentInboundActivityResponse(BaseModel):
    inbound_item_id: UUID
    book_title: str
    inbound_type: InboundType
    inbound_status: InboundStatus
    quantity: int = Field(ge=0)
    location_barcode: str | None = None
    occurred_at: datetime


class InboundDashboardSummaryResponse(BaseModel):
    today_inbound_quantity: int = Field(
        ge=0,
        description="금일 실제 Inventory 입고 수량",
    )
    completed_inspection_count: int = Field(
        ge=0,
        description="금일 최종 처리된 중고·반품 검수 건수",
    )
    pending_inspection_count: int = Field(
        ge=0,
        description="처리 대기 또는 진행 중인 중고·반품 검수 건수",
    )
    recheck_required_count: int = Field(
        ge=0,
        description="재촬영이 필요한 중고·반품 검수 건수",
    )
    daily_inbound_trend: list[InboundDashboardTrendItem]
    grade_distribution: list[InboundDashboardGradeItem]
    zone_stocks: list[InboundDashboardZoneItem]
    recent_activities: list[RecentInboundActivityResponse]
