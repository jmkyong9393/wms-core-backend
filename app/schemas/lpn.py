from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.wms import (
    BookCategory,
    ConditionGrade,
    InboundType,
    PutawayStatus,
    UsedInventoryStatus,
)


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
                    "m7sX0zYV2wF6U3pG8nR4cQ1aK9tB5eHjL0dSxWvNqPo"
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


class LpnPutawayBookDetail(BaseModel):
    id: UUID = Field(description="도서 마스터 ID")
    isbn: str | None = Field(description="도서 ISBN")
    title: str = Field(description="도서명")
    category: BookCategory = Field(description="Rack 배정에 사용된 도서 카테고리")


class LpnPutawayResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "lpn_barcode": "LPN-12345678123456781234567812345678",
                "inbound_item_id": "00000000-0000-4000-8000-000000000001",
                "inbound_type": "USED_PURCHASE",
                "book": {
                    "id": "00000000-0000-4000-8000-000000000002",
                    "isbn": "9781234567890",
                    "title": "사피엔스",
                    "category": "HUMANITIES",
                },
                "condition_grade": "EXCELLENT",
                "putaway_job_id": "00000000-0000-4000-8000-000000000003",
                "putaway_status": "WAITING",
                "location": {
                    "id": "00000000-0000-4000-8000-000000000004",
                    "barcode": "B-4-1",
                    "zone": "B",
                    "rack": "4",
                    "shelf": "1",
                },
            }
        }
    )

    lpn_barcode: str = Field(description="작업자가 스캔한 단품 LPN")
    inbound_item_id: UUID = Field(description="LPN이 발급된 입고 품목 ID")
    inbound_type: InboundType = Field(description="신간·중고 매입·고객 반품 유형")
    book: LpnPutawayBookDetail = Field(description="적재 대상 도서 정보")
    condition_grade: ConditionGrade = Field(description="로케이션 배정 기준 등급")
    putaway_job_id: UUID = Field(description="적재 작업 ID")
    putaway_status: PutawayStatus = Field(description="적재 작업 진행 상태")
    location: LpnLocationDetail = Field(description="정책으로 확정된 적재 위치")
