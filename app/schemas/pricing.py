from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.wms import BookCategory, ConditionGrade


class DynamicPricingContextResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "inventory_used_item_id": (
                    "00000000-0000-4000-8000-000000000001"
                ),
                "lpn_barcode": "LPN-12345678123456781234567812345678",
                "book_id": "00000000-0000-4000-8000-000000000002",
                "isbn": "9788912345678",
                "base_price": "18000.00",
                "category": "NOVEL",
                "ubci_score": "91.50",
                "condition_grade": "EXCELLENT",
            }
        }
    )

    inventory_used_item_id: UUID = Field(
        description="가격 책정 대상 중고·반품 단품 재고 ID"
    )
    lpn_barcode: str = Field(description="가격 책정 대상 LPN 바코드")
    book_id: UUID = Field(description="연결된 도서 마스터 ID")
    isbn: str | None = Field(default=None, description="도서 ISBN")
    base_price: Decimal = Field(
        gt=0,
        max_digits=12,
        decimal_places=2,
        description="할인 적용 전 도서 정가",
    )
    category: BookCategory = Field(
        description="알라딘 카테고리를 변환한 WMS 내부 도서 카테고리"
    )
    ubci_score: Decimal = Field(
        ge=0,
        le=100,
        max_digits=5,
        decimal_places=2,
        description="AI 검수로 확정된 UBCI 점수",
    )
    condition_grade: ConditionGrade = Field(
        description="UBCI 등급 정책으로 확정되어 저장된 품질 등급"
    )


class DynamicPricingResultRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "lpn_barcode": "LPN-12345678123456781234567812345678",
                "discount_rate": "0.1500",
                "final_price": "15300.00",
            }
        }
    )

    lpn_barcode: str = Field(
        min_length=1,
        description="가격 산정 대상 중고·반품 단품의 LPN 바코드",
    )
    discount_rate: Decimal = Field(
        ge=0,
        lt=1,
        max_digits=5,
        decimal_places=4,
        description="Agent가 산정한 할인율. 0 이상 1 미만의 소수",
    )
    final_price: Decimal = Field(
        gt=0,
        max_digits=12,
        decimal_places=2,
        description="Agent가 산정한 해당 LPN의 판매가격",
    )


class DynamicPricingResultResponse(BaseModel):
    inventory_used_item_id: UUID = Field(
        description="가격이 저장된 중고·반품 단품 재고 ID"
    )
    lpn_barcode: str = Field(description="가격이 저장된 LPN 바코드")
    base_price: Decimal = Field(description="도서 마스터의 정가")
    discount_rate: Decimal = Field(description="저장된 할인율")
    sale_price: Decimal = Field(description="저장된 LPN 판매가격")
    pricing_changed: bool = Field(
        description="이번 요청으로 저장 가격이 변경되었는지 여부"
    )
