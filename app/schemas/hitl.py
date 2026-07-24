from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.models.wms import ReturnJobStatus


# 관리자가 HITL 검수 작업에 내릴 수 있는 판단 종류
class HITLAction(str, Enum):
    APPROVE_NORMAL = "APPROVE_NORMAL"
    APPROVE_DOWNGRADE = "APPROVE_DOWNGRADE"
    REJECT_RETURN = "REJECT_RETURN"
    REJECT_DISCARD = "REJECT_DISCARD"
    RE_CHECK = "RE_CHECK"


# 관리자 판단 사유 코드
class HITLReasonCode(str, Enum):
    # 정상 승인용 AI 오탐 코드
    FP_SHADOW = "FP_SHADOW"
    FP_COVER_PATTERN = "FP_COVER_PATTERN"
    FP_GLARE = "FP_GLARE"
    FP_BOOK_BAND = "FP_BOOK_BAND"
    FP_DUST = "FP_DUST"
    FP_OTHER = "FP_OTHER"

    # 외부 파손 코드
    DMG_EXT_WET = "DMG_EXT_WET"
    DMG_EXT_CRUSH = "DMG_EXT_CRUSH"
    DMG_EXT_TEAR = "DMG_EXT_TEAR"
    DMG_EXT_BOX = "DMG_EXT_BOX"
    DMG_EXT_OTHER = "DMG_EXT_OTHER"

    # 내부 결함 코드
    DMG_INT_BINDING = "DMG_INT_BINDING"
    DMG_INT_STAIN = "DMG_INT_STAIN"
    DMG_INT_DISCOLOR = "DMG_INT_DISCOLOR"
    DMG_INT_BARCODE = "DMG_INT_BARCODE"
    DMG_INT_OTHER = "DMG_INT_OTHER"

    # 재검토·예외 코드
    SYS_BLURRY = "SYS_BLURRY"
    SYS_WRONG_ITEM = "SYS_WRONG_ITEM"
    SYS_MISSING_PARTS = "SYS_MISSING_PARTS"
    SYS_OTHER = "SYS_OTHER"


# 하향 승인 시 선택할 수 있는 등급
class HITLTargetGrade(str, Enum):
    EXCELLENT = "EXCELLENT"
    NORMAL = "NORMAL"


# HITL 관리자 판단 API 요청 데이터
class HITLDecisionRequest(BaseModel):
    action: HITLAction

    reviewer_reason_code: HITLReasonCode

    target_grade: HITLTargetGrade | None = None

    comment: str | None = Field(
        default=None,
        max_length=1000,
    )

    @model_validator(mode="after")
    def validate_target_grade(self) -> "HITLDecisionRequest":
        if self.action == HITLAction.APPROVE_DOWNGRADE:
            if self.target_grade is None:
                raise ValueError(
                    "APPROVE_DOWNGRADE 판단에는 "
                    "target_grade EXCELLENT 또는 NORMAL이 필요합니다."
                )

        elif self.target_grade is not None:
            raise ValueError(
                "target_grade는 APPROVE_DOWNGRADE 판단에서만 사용할 수 있습니다."
            )

        return self

# HITL 관리자 판단 API 응답 데이터
class HITLDecisionResponse(BaseModel):
    job_id: UUID
    action: HITLAction
    status: ReturnJobStatus

    # RE_CHECK는 즉시 Celery 작업을 생성하지 않으므로 None 가능
    task_id: str | None = None

    message: str