from decimal import Decimal

from pydantic import BaseModel, Field

from app.models.wms import (
    ConditionGrade,
    InboundStatus,
    InboundType,
    RejectedItemStatus,
    ReturnJobStatus,
    UsedInventoryStatus,
)
from app.schemas.lpn import (
    LpnBookDetail,
    LpnLocationDetail,
)


class LpnScanResponse(BaseModel):
    """
    작업자 모바일 앱이 LPN QR을 스캔했을 때 표시할 내부 업무 정보다.

    입고 직후, AI 검수 중, 재촬영 요청, 검수 완료, 판매 가능 재고 편입,
    검수 반려 상태를 동일한 certificate_token으로 조회한다.
    """

    lpn_barcode: str = Field(
        description="책에 부착된 물리 단품 LPN",
    )
    book: LpnBookDetail = Field(
        description="스캔한 단품의 도서 정보",
    )

    inbound_type: InboundType = Field(
        description="중고 매입 또는 고객 반품 입고 유형",
    )
    inbound_status: InboundStatus = Field(
        description="입고 작업 상태",
    )
    inspection_status: ReturnJobStatus | None = Field(
        default=None,
        description="AI 검수 작업 상태. 아직 검수 작업이 없으면 null",
    )

    final_grade: ConditionGrade | None = Field(
        default=None,
        description="검수 또는 관리자 판단으로 확정된 도서 등급",
    )
    ubci_score: Decimal | None = Field(
        default=None,
        description="확정된 UBCI 점수",
    )

    inventory_status: UsedInventoryStatus | None = Field(
        default=None,
        description="판매 가능 중고 단품 재고 상태",
    )
    rejected_item_status: RejectedItemStatus | None = Field(
        default=None,
        description="반려 단품 상태",
    )

    location: LpnLocationDetail | None = Field(
        default=None,
        description=(
            "검수 완료 후 배정·등록된 현재 보관 로케이션. "
            "검수 전이면 null"
        ),
    )

    requires_retake: bool = Field(
        description="AI 또는 관리자가 재촬영을 요청한 상태인지 여부",
    )