from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


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

    # 반려된 InboundItem 수량. 연결 품목이 없으면 서비스에서 1권으로 처리
    rejected_quantity: int = Field(
        gt=0,
        description="이번 검수에서 반려된 수량",
    )

    # 관리자 HITL 사유 코드 우선, 없으면 AI reason_code 사용
    rejection_reason_code: str = Field(
        min_length=1,
        description="반려 사유 코드",
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