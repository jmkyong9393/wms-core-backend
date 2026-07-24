from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.wms import InboundStatus, InboundType


UsedInboundType = Literal[
    InboundType.USED_PURCHASE,
    InboundType.CUSTOMER_RETURN,
]


class UsedBookInboundRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "inbound_type": "USED_PURCHASE",
                "book_id": "00000000-0000-4000-8000-000000000001",
                "supplier_name": "홍길동",
            }
        }
    )

    inbound_type: UsedInboundType = Field(
        description="중고 매입 또는 고객 반품 입고 유형",
    )
    book_id: UUID = Field(description="검수할 도서 마스터 ID")
    supplier_name: str | None = Field(
        default=None,
        description="중고 매입 공급자명. 고객 반품에서는 생략 가능",
    )


class UsedBookInboundResponse(BaseModel):
    inbound_id: UUID = Field(description="생성된 입고 작업 ID")
    inbound_item_id: UUID = Field(description="검수 대상 물리 도서의 입고 품목 ID")
    inbound_type: InboundType = Field(description="입고 유형")
    status: InboundStatus = Field(description="입고 작업 상태")
    book_id: UUID = Field(description="도서 마스터 ID")
    lpn_barcode: str = Field(description="검수 전 발급된 단품 추적 LPN")
