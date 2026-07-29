from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.wms import ConditionGrade
from app.schemas.hitl import HITLReasonCode

InspectionDecision = Literal["APPROVE", "REJECT"]

RejectionDisposition = Literal[
    "REJECT_RETURN",
    "REJECT_DISCARD",
]


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
                "admin_decision_code": "FP_SHADOW",
                "final_grade": "NORMAL",
                "rejection_disposition": None,
            }
        }
    )

    return_job_id: UUID = Field(description="재고 편입의 근거가 되는 검수 작업 ID")
    decision: InspectionDecision = Field(description="AI 최종 승인 또는 반려 결정")
    ubci_score: Decimal | None = Field(
        default=None,
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
    admin_decision_code: HITLReasonCode | None = Field(
        default=None,
        description="관리자 HITL 판정 사유 코드",
    )
    final_grade: ConditionGrade | None = Field(
        default=None,
        description="관리자가 확정한 최종 도서 등급",
    )
    rejection_disposition: RejectionDisposition | None = Field(
        default=None,
        description="반려 도서 처리 방식",
    )
    @model_validator(mode="after")
    def validate_approved_result(self):
        if (
            self.decision == "APPROVE"
            and self.ubci_score is None
            and self.final_grade is None
        ):
            raise ValueError(
                "ubci_score or final_grade is required for approval"
            )
        return self


class InspectionInventoryResponse(BaseModel):
    return_job_id: UUID = Field(description="검수 작업 ID")
    inbound_item_id: UUID = Field(description="검수 대상 입고 품목 ID")
    decision: InspectionDecision = Field(description="적용된 승인 또는 반려 결정")
    condition_grade: ConditionGrade = Field(description="UBCI 정책으로 확정된 등급")
    lpn_barcode: str = Field(description="검수 대상 단품 LPN")
    location_id: UUID = Field(description="등급과 카테고리로 확정된 로케이션 ID")
    location_barcode: str = Field(description="확정된 로케이션 바코드")
    inventory_used_item_id: UUID | None = Field(
        description="판매 가능 중고·반품 단품 재고 ID",
    )
    rejected_item_id: UUID | None = Field(
        description="REJECT 판정 도서의 폐기 대기 레코드 ID",
    )
    inventory_changed: bool = Field(description="재고 또는 폐기 대기 기록 생성 여부")
