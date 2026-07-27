from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.wms import ConditionGrade, UsedInventoryStatus


class LpnBookDetail(BaseModel):
    id: UUID = Field(description="도서 마스터 ID")
    isbn: str | None = Field(description="도서 ISBN")
    title: str = Field(description="도서명")
    publisher: str | None = Field(description="출판사")


class LpnLocationDetail(BaseModel):
    id: UUID = Field(description="현재 보관 로케이션 ID")
    barcode: str | None = Field(description="현재 보관 로케이션 바코드")
    zone: str = Field(description="창고 구역")
    rack: str = Field(description="랙")
    shelf: str = Field(description="선반")


class LpnDetailResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "lpn_barcode": "LPN-12345678123456781234567812345678",
                "book": {
                    "id": "00000000-0000-4000-8000-000000000001",
                    "isbn": "9781234567890",
                    "title": "사피엔스",
                    "publisher": "김영사",
                },
                "inventory_status": "AVAILABLE",
                "condition_grade": "EXCELLENT",
                "ubci_score": "91.50",
                "location": {
                    "id": "00000000-0000-4000-8000-000000000002",
                    "barcode": "A-1-3",
                    "zone": "A",
                    "rack": "1",
                    "shelf": "3",
                },
                "stocked_at": "2026-07-27T10:00:00",
                "certificate_url": (
                    "https://wms.example.com/certificate/"
                    "LPN-12345678123456781234567812345678"
                ),
            }
        }
    )

    lpn_barcode: str = Field(description="물리 도서 한 권을 식별하는 LPN")
    book: LpnBookDetail = Field(description="LPN에 연결된 도서 마스터 정보")
    inventory_status: UsedInventoryStatus = Field(
        description="단품 재고의 현재 가용 상태",
    )
    condition_grade: ConditionGrade = Field(
        description="검수 완료 후 확정된 품질 등급",
    )
    ubci_score: Decimal | None = Field(
        default=None,
        ge=0,
        le=100,
        max_digits=5,
        decimal_places=2,
        description="검수 완료 후 확정된 UBCI 점수",
    )
    location: LpnLocationDetail = Field(
        description="단품 재고의 현재 물리적 보관 위치",
    )
    stocked_at: datetime = Field(description="판매 가능 재고에 편입된 시각")
    certificate_url: str = Field(
        description="LPN 라벨 QR에 인코딩할 공개 품질보증서 URL",
    )
