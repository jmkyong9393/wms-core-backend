from decimal import Decimal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)


class PricingRecommendationRequest(BaseModel):
    """백엔드에서 Pricing Agent로 전달하는 입력값."""

    model_config = ConfigDict(
        extra="forbid",
    )

    book_title: str = Field(
        min_length=1,
        description="도서명",
    )

    category: str = Field(
        min_length=1,
        description="도서 카테고리",
    )

    original_price: Decimal = Field(
        gt=0,
        description="도서 정가",
    )

    ubci_score: float = Field(
        ge=0,
        le=100,
        description="비전 검수 기반 UBCI 점수",
    )

    condition_grade: str = Field(
        min_length=1,
        description="도서 품질 등급",
    )

    base_calculated_price: Decimal = Field(
        gt=0,
        description="백엔드 룰베이스 기초 책정가",
    )

    virtual_demand: str = Field(
        min_length=1,
        description="백엔드에서 전달한 가상 수요",
    )


class PricingReason(BaseModel):
    """LLM이 생성하는 가격 책정 사유."""

    model_config = ConfigDict(
        extra="forbid",
    )

    pricing_reason: str = Field(
        min_length=20,
        max_length=500,
        description="가격 책정 사유 2~3문장",
    )


class PricingRecommendationResponse(BaseModel):
    """Pricing Agent 최종 반환값."""

    model_config = ConfigDict(
        extra="forbid",
    )

    final_price: int = Field(
        ge=0,
        multiple_of=100,
        description="100원 단위 최종 책정가",
    )

    pricing_reason: str = Field(
        min_length=20,
        max_length=500,
        description="가격 책정 사유 2~3문장",
    )