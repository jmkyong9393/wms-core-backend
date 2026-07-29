from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.wms import (
    BookCategory,
    ConditionGrade,
    InboundStatus,
    InboundType,
    StandardSize,
)


class NewStockInboundRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "isbn": "9788912345678",
                "title": "해리포터와 마법사의 돌",
                "publisher": "문학수첩",
                "category": "NOVEL",
                "base_price": "15000.00",
                "standard_size": "A5",
                "thickness_mm": 20,
                "supplier_name": "교보문고",
            }
        }
    )

    isbn: str = Field(
        min_length=10,
        max_length=13,
        description="외부 도서 API로 조회한 ISBN",
    )
    title: str = Field(min_length=1, description="도서명")
    publisher: str | None = Field(default=None, description="출판사명")
    category: BookCategory = Field(description="내부 기준으로 변환된 도서 카테고리")
    base_price: Decimal = Field(gt=0, description="도서 기준 판매가")
    standard_size: StandardSize | None = Field(
        default=None,
        description="3D Bin Packing용 도서 규격",
    )
    thickness_mm: int | None = Field(
        default=None,
        gt=0,
        description="도서 두께(mm)",
    )
    supplier_name: str | None = Field(
        default=None,
        description="신간 공급처명",
    )


class NewStockInboundResponse(BaseModel):
    inbound_id: UUID = Field(description="생성된 신간 입고 작업 ID")
    inbound_item_id: UUID = Field(description="물리 도서 1권의 입고 품목 ID")
    inbound_type: InboundType = Field(description="NEW_STOCK 입고 유형")
    status: InboundStatus = Field(description="입고 작업 상태")
    book_id: UUID = Field(description="도서 마스터 ID")
    condition_grade: ConditionGrade = Field(description="신간 고정 등급 NEW")
    lpn_barcode: str = Field(description="신간 단품 추적 LPN")
    certificate_url: str = Field(description="LPN 라벨 QR에 인코딩할 공개 URL")
    location_id: UUID = Field(description="정책으로 확정된 적재 로케이션 ID")
    location_barcode: str = Field(description="정책으로 확정된 적재 로케이션 바코드")
    inventory_id: UUID = Field(description="즉시 편입된 신간 묶음 재고 ID")
    inventory_quantity: int = Field(description="편입 후 해당 로케이션의 재고 수량")
