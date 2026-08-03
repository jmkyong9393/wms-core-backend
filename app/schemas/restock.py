from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.wms import (
    OrderProposalStatus,
    RestockProposalSource,
)


def to_camel(field_name: str) -> str:
    parts = field_name.split("_")

    return parts[0] + "".join(
        word.capitalize()
        for word in parts[1:]
    )


class RestockRecommendationRequest(BaseModel):
    """
    Restock Agent에 전달하는 반려 기반 대체 발주 추천 입력값.

    최종 반려된 ReturnJob을 기준으로 서비스가 최근 판매량,
    현재 가용 재고, 반려 수량, 반려 사유를 조회해 생성한다.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    isbn: str = Field(
        min_length=1,
        description="반려된 도서의 ISBN",
    )

    book_title: str = Field(
        min_length=1,
        description="반려된 도서명",
    )

    # 최근 7일간 출고 완료된 B2B 주문 수량
    recent_sales_quantity: int = Field(
        ge=0,
        description="임시 최근 판매량",
    )

    # 신간 가용 재고와 AVAILABLE 중고 LPN을 합산한 수량
    current_stock: int = Field(
        ge=0,
        description="임시 현재 가용 재고",
    )

    # 이미 생성되어 입고를 기다리는 AUTO_PO 수량
    pending_auto_po_quantity: int = Field(
        default=0,
        ge=0,
        description="진행 중인 AUTO_PO 입고 예정 수량",
    )

    # 반려된 InboundItem 수량. 연결 품목이 없으면 서비스에서 1권으로 처리
    rejected_quantity: int = Field(
        ge=0,
        description="이번 검수에서 반려된 수량",
    )

    # 관리자 HITL 사유 코드 우선, 없으면 AI reason_code 사용
    rejection_reason_code: str = Field(
        min_length=1,
        description="반려 사유 코드",
    )

    proposal_source: RestockProposalSource = (
        RestockProposalSource.RETURN_REJECTION
    )

    safety_stock_quantity: int | None = Field(
        default=None,
        ge=0,
        description="안전재고 기준 수량. 반려 대체 발주에서는 null",
    )


class RestockRecommendationResponse(BaseModel):
    """
    Restock Agent가 반환하는 대체 발주 추천 결과.

    최종 반려 건에서 생성된 결과는 OrderProposal에 저장되며,
    추천 수량이 1권 이상이면 관리자 알림센터로 발행된다.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    isbn: str
    book_title: str

    # 0이면 추가 대체 발주가 필요 없음을 의미
    recommended_order_quantity: int = Field(
        ge=0,
        description="LLM이 추천한 0 이상의 발주 수량",
    )

    # OrderProposal에 저장할 발주 추천 사유
    reason_summary: str

    # OrderProposal에 저장할 판단 근거
    evidence: list[str] = Field(
        default_factory=list,
    )

    # OrderProposal 및 RESTOCK_ALERT 중요도에 사용
    risk_level: Literal[
        "HIGH",
        "MEDIUM",
        "LOW",
    ]

class RestockProposalBookResponse(BaseModel):
    """
    Restock 추천안 화면에 함께 반환하는 도서 기본 정보.
    """
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    id: UUID
    title: str
    isbn: str | None = None
    publisher: str | None = None


class RestockProposalListItemResponse(BaseModel):
    """
    관리자 Restock 추천안 목록 조회용 요약 응답.

    목록 화면에서 추천 상태, 수량, 위험도와 판단 당시 재고 현황을
    빠르게 확인하는 데 사용한다.
    """
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    id: UUID
    book: RestockProposalBookResponse

    status: OrderProposalStatus
    recommended_order_quantity: int
    risk_level: Literal["HIGH", "MEDIUM", "LOW"]

    recent_sales_quantity: int
    current_stock: int
    proposal_source: RestockProposalSource
    pending_auto_po_quantity: int
    rejected_quantity: int

    created_at: datetime
    reviewed_at: datetime | None = None


class RestockProposalDetailResponse(BaseModel):
    """
    관리자 Restock 추천안 상세 조회용 응답.

    Agent 입력 스냅샷, 추천 근거, 검토 이력, 실제 AUTO_PO 연결 정보를
    포함한다.
    """
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    id: UUID
    book: RestockProposalBookResponse

    return_job_id: UUID | None = None
    proposal_source: RestockProposalSource
    status: OrderProposalStatus

    recent_sales_quantity: int
    current_stock: int
    pending_auto_po_quantity: int
    rejected_quantity: int
    rejection_reason_code: str

    recommended_order_quantity: int
    reason_summary: str
    evidence: list[str] = Field(default_factory=list)
    risk_level: Literal["HIGH", "MEDIUM", "LOW"]

    auto_po_order_id: UUID | None = None

    reviewer_id: UUID | None = None
    reviewer_employee_id: str | None = None
    reviewed_at: datetime | None = None
    review_comment: str | None = None

    created_at: datetime
    updated_at: datetime


class RestockProposalReviewRequest(BaseModel):
    """
    관리자가 PENDING 상태의 추천안을 승인하거나 반려할 때 전달하는 입력값.
    """
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    comment: str | None = Field(
        default=None,
        max_length=1000,
        description="관리자 승인 또는 반려 의견",
    )


class RestockProposalReviewResponse(BaseModel):
    """
    관리자 승인 또는 반려 처리 결과 응답.

    승인된 경우 연결된 AUTO_PO 주문 ID를 함께 반환한다.
    """
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    proposal_id: UUID
    status: OrderProposalStatus
    auto_po_order_id: UUID | None = None
    reviewed_at: datetime
    message: str