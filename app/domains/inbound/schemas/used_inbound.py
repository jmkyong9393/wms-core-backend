from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domains.lpn.schemas.label import LabelPrintStatus
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
    certificate_url: str | None = Field(
        description=("일반 소비자가 품질보증서를 직접 조회하는 공개 URL. 검수 완료 전에는 품질보증서를 조회할 수 없다.")
    )
    label_scan_url: str = Field(
        description=(
            "실제 LPN 라벨 QR에 인코딩하는 공통 스캔 URL. "
            "작업자 앱은 내부 LPN 상세 화면으로, 일반 브라우저는 "
            "공개 품질보증서 화면으로 분기한다."
        )
    )
    label_print_status: LabelPrintStatus = Field(
        description=(
            "이번 요청에서의 라벨 전송 결과. "
            "SENT=프린터 전송 완료, "
            "SKIPPED=프린터 비활성화 또는 중복 입고 요청, "
            "FAILED=프린터 전송 실패"
        )
    )
    label_print_error: str | None = Field(
        default=None,
        description=("라벨 전송 실패 시 작업자에게 표시할 안내 메시지. 프린터 IP 등 내부 설정 정보는 포함하지 않는다."),
    )
