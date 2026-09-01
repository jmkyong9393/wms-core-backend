from decimal import Decimal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)

from app.models.wms import (
    BookCategory,
    ConditionGrade,
)


class DynamicPricingContextResponse(BaseModel):
    """백엔드가 AI에 제공하는 가격 책정 Context."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "inventory_used_item_id": ("00000000-0000-4000-8000-000000000001"),
                "lpn_barcode": ("LPN-12345678123456781234567812345678"),
                "book_id": ("00000000-0000-4000-8000-000000000002"),
                "isbn": "9788912345678",
                "base_price": "18000.00",
                "category": "NOVEL",
                "ubci_score": "91.50",
                "condition_grade": "EXCELLENT",
            }
        }
    )

    inventory_used_item_id: UUID = Field(
        description="가격 책정 대상 중고·반품 단품 재고 ID",
    )

    lpn_barcode: str = Field(
        description="가격 책정 대상 LPN 바코드",
    )

    book_id: UUID = Field(
        description="연결된 도서 마스터 ID",
    )

    isbn: str | None = Field(
        default=None,
        description="도서 ISBN",
    )

    base_price: Decimal = Field(
        gt=0,
        max_digits=12,
        decimal_places=2,
        description="할인 적용 전 도서 정가",
    )

    category: BookCategory = Field(
        description=("알라딘 카테고리를 변환한 WMS 내부 도서 카테고리"),
    )

    ubci_score: Decimal = Field(
        ge=0,
        le=100,
        max_digits=5,
        decimal_places=2,
        description="AI 검수로 확정된 UBCI 점수",
    )

    condition_grade: ConditionGrade = Field(
        description="저장된 품질 등급",
    )


class PricingRecommendationRequest(BaseModel):
    """백엔드 Context 기반 AI 가격 책정 입력값."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    base_price: Decimal = Field(
        ge=100,
        max_digits=12,
        decimal_places=2,
        description="할인 적용 전 도서 정가",
    )

    category: BookCategory = Field(
        description="백엔드 표준 도서 카테고리",
    )

    ubci_score: float = Field(
        ge=65,
        le=100,
        description="가격 책정 가능한 도서의 UBCI 점수",
    )

    condition_grade: ConditionGrade = Field(
        description="가격 설명용 품질 등급",
    )


class PricingReason(BaseModel):
    """LLM 가격 선정 사유 출력값."""

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
    """AI Pricing Agent 최종 반환값."""

    model_config = ConfigDict(
        extra="forbid",
    )

    final_price: int = Field(
        ge=100,
        multiple_of=100,
        description="100원 단위 최종 판매가격",
    )

    discount_rate: int = Field(
        ge=0,
        le=100,
        description=("정가 대비 정수 할인율. 34는 34퍼센트를 의미"),
    )

    pricing_reason: str = Field(
        min_length=20,
        max_length=500,
        description="LLM이 작성한 가격 선정 사유",
    )


class DynamicPricingResultRequest(BaseModel):
    """백엔드 가격 저장 요청값."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "lpn_barcode": ("LPN-12345678123456781234567812345678"),
                "discount_rate": "0.1500",
                "final_price": "15300.00",
            }
        }
    )

    lpn_barcode: str = Field(
        min_length=1,
        description="가격 산정 대상 LPN 바코드",
    )

    discount_rate: Decimal = Field(
        ge=0,
        lt=1,
        max_digits=5,
        decimal_places=4,
        description="백엔드 저장용 소수 할인율",
    )

    final_price: Decimal = Field(
        gt=0,
        max_digits=12,
        decimal_places=2,
        description="Agent가 산정한 최종 판매가격",
    )


class DynamicPricingResultResponse(BaseModel):
    """백엔드 가격 저장 결과값."""

    inventory_used_item_id: UUID = Field(
        description="가격이 저장된 단품 재고 ID",
    )

    lpn_barcode: str = Field(
        description="가격이 저장된 LPN 바코드",
    )

    base_price: Decimal = Field(
        description="도서 마스터의 정가",
    )

    discount_rate: Decimal = Field(
        description="저장된 소수 할인율",
    )

    sale_price: Decimal = Field(
        description="저장된 LPN 판매가격",
    )

    pricing_changed: bool = Field(
        description="저장 가격 변경 여부",
    )
