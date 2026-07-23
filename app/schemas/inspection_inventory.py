from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.wms import ConditionGrade, UsedInventoryStatus


InspectionDecision = Literal["APPROVE", "REJECT"]


class InspectionInventoryRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "return_job_id": "00000000-0000-4000-8000-000000000001",
                "decision": "APPROVE",
                "ubci_score": "92.50",
                "defects": [
                    {
                        "type": "COVER_SCRATCH",
                        "ratio": 0.03,
                    }
                ],
                "location_barcode": "USED-A-1-1",
            }
        }
    )

    return_job_id: UUID = Field(description="재고 편입의 근거가 되는 검수 작업 ID")
    decision: InspectionDecision = Field(description="AI 최종 승인 또는 반려 결정")
    ubci_score: Decimal = Field(
        ge=0,
        le=100,
        max_digits=5,
        decimal_places=2,
        description="AI가 계산한 최종 UBCI 점수",
    )
    defects: list[dict[str, Any]] = Field(
        default_factory=list,
        description="등급 정책의 치명적 결함 판정에 사용할 결함 목록",
    )
    location_barcode: str | None = Field(
        default=None,
        min_length=1,
        description="승인된 LPN을 적재할 활성 로케이션 바코드",
    )

    @model_validator(mode="after")
    def validate_approved_location(self):
        if self.decision == "APPROVE" and not self.location_barcode:
            raise ValueError("location_barcode is required for approval")
        return self


class InspectionInventoryResponse(BaseModel):
    return_job_id: UUID = Field(description="검수 작업 ID")
    inbound_item_id: UUID = Field(description="검수 대상 입고 품목 ID")
    decision: InspectionDecision = Field(description="적용된 승인 또는 반려 결정")
    condition_grade: ConditionGrade = Field(description="UBCI 정책으로 확정된 등급")
    lpn_barcode: str = Field(description="검수 대상 단품 LPN")
    inventory_used_item_id: UUID | None = Field(
        default=None,
        description="승인 시 생성된 중고 단품 재고 ID",
    )
    inventory_status: UsedInventoryStatus | None = Field(
        default=None,
        description="승인된 단품 재고 상태",
    )
    inventory_changed: bool = Field(
        description="이번 요청에서 신규 재고 편입이 발생했는지 여부",
    )
