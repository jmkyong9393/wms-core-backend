from decimal import Decimal
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)


# 백엔드 BookCategory와 동일한 카테고리 값
BookCategory = Literal[
    "COMIC",
    "STUDY_GUIDE",
    "NOVEL",
    "HUMANITIES",
    "SOCIAL_SCIENCE",
    "BUSINESS_ECONOMICS",
    "SCIENCE_TECHNOLOGY",
    "CHILDREN",
    "LANGUAGE",
    "ART_LIFESTYLE",
]


class PricingRecommendationRequest(BaseModel):
    """백엔드 Context 기반 가격 책정 입력값."""

    # 정의되지 않은 입력 필드 차단 및 문자열 공백 제거
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    base_price: Decimal = Field(
        gt=0,
        max_digits=12,
        decimal_places=2,
        description="감가 적용 전 기준가격",
    )

    category: BookCategory = Field(
        description="백엔드 표준 도서 카테고리",
    )

    ubci_score: float = Field(
        ge=65,
        le=100,
        description="가격 책정 가능한 도서의 UBCI 점수",
    )

    condition_grade: str = Field(
        min_length=1,
        max_length=20,
        pattern=r"^[A-Z][A-Z0-9_]*$",
        description="가격 설명용 품질 등급",
    )


class PricingReason(BaseModel):
    """LLM 가격 선정 사유 출력값."""

    # LLM의 예상하지 않은 출력 필드 차단
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    pricing_reason: str = Field(
        min_length=20,
        max_length=500,
        description="검증된 가격 정보를 이용한 선정 사유",
    )


class PricingRecommendationResponse(BaseModel):
    """Pricing Agent 최종 반환값."""

    # 정의되지 않은 응답 필드 차단
    model_config = ConfigDict(
        extra="forbid",
    )

    final_price: int = Field(
        ge=100,
        multiple_of=100,
        description="100원 단위 최종 판매가격",
    )

    pricing_reason: str = Field(
        min_length=20,
        max_length=500,
        description="LLM이 작성한 가격 선정 사유",
    )