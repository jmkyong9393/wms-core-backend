from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.wms import ConditionGrade, PutawayStatus
from app.schemas.lpn import LpnLocationDetail


PutawayInventoryKind = Literal[
    "NEW_AGGREGATE",
    "USED_ITEM",
    "REJECT_HOLD",
]


class PutawayConfirmationResponse(BaseModel):
    lpn_barcode: str = Field(description="적재를 완료한 단품 LPN")
    inbound_item_id: UUID = Field(description="입고 품목 ID")
    putaway_job_id: UUID = Field(description="적재 작업 ID")
    putaway_status: PutawayStatus = Field(description="COMPLETED 적재 상태")
    condition_grade: ConditionGrade = Field(description="입고 확정 등급")
    location: LpnLocationDetail = Field(description="실제 적재 완료 로케이션")
    inventory_kind: PutawayInventoryKind = Field(
        description="편입된 재고 유형 또는 C Zone 보관 유형",
    )
    inventory_id: UUID | None = Field(
        default=None,
        description="생성 또는 증가한 판매 재고 ID. REJECT는 없음",
    )
    stock_changed: bool = Field(
        description="이번 요청에서 판매 가능 재고 수량이 변경됐는지 여부",
    )
