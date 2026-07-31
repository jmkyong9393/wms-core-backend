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
