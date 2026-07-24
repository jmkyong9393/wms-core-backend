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
    자동 발주 추천 Agent에 전달할 임시 입력 스키마.

    TODO:
    반려 처리 API와 재고·판매량 조회 로직이 확정되면
    실제 전달 데이터 규격에 맞춰 필드명과 타입을 수정해야 한다.

    현재는 Restock Agent 흐름을 테스트하기 위한 Mock 규격이다.
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

    # TODO: 실제 판매량 집계 기간과 데이터 출처 확인 필요
    recent_sales_quantity: int = Field(
        ge=0,
        description="임시 최근 판매량",
    )

    # TODO: 실제 가용 재고 계산 기준 확인 필요
    current_stock: int = Field(
        ge=0,
        description="임시 현재 가용 재고",
    )

    # TODO: 실제 반려 처리 결과에서 전달받을 수량 필드 확인 필요
    rejected_quantity: int = Field(
        gt=0,
        description="이번 검수에서 반려된 수량",
    )

    # TODO: 반려 API의 최종 사유 코드 규격과 맞춰야 함
    rejection_reason_code: str = Field(
        min_length=1,
        description="반려 사유 코드",
    )


class RestockRecommendationResponse(BaseModel):
    """
    자동 발주 추천 Agent가 반환할 임시 출력 스키마.

    TODO:
    order_proposals 테이블 및 칸반보드 API 규격이 확정되면
    실제 저장 컬럼과 프론트 응답 형식에 맞춰 수정해야 한다.

    현재는 Agent 결과를 JSON으로 확인하기 위한 Mock 규격이다.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    isbn: str
    book_title: str

    # TODO: 실제 발주 수량 산정 정책 확정 후 검증 규칙 추가
    recommended_order_quantity: int = Field(
        ge=0,
        description="LLM이 추천한 0 이상의 발주 수량",
    )

    # TODO: order_proposals의 발주 사유 컬럼과 연결 예정
    reason_summary: str

    # TODO: 최종 DB에 저장할지, API 응답에서만 사용할지 확인 필요
    evidence: list[str] = Field(
        default_factory=list,
    )

    # TODO: 위험도 값과 기준은 임시이며 정책 확정 후 변경 필요
    risk_level: Literal[
        "HIGH",
        "MEDIUM",
        "LOW",
    ]