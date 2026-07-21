from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.wms import ReturnJobStatus

# 관리자가 HITL 검수 작업에 내릴 수 있는 판단 종류
class HITLAction(str, Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    RECALCULATE = "RECALCULATE"

# HITL 관리 판단 API의 요청 데이터
class HITLDecisionRequest(BaseModel):
    action: HITLAction

    # AI Critic의 reason_code와 구분되는 관리자 판단 사유 코드
    # 추후 관리자용 공통 사유 코드가 확정되면 Enum으로 변경 가능
    reviewer_reason_code: str | None = Field(
        default=None,
        max_length=100,
    )

    # 관리자가 남기는 추가 설명
    comment: str | None = Field(
        default=None,
        max_length=1000,
    )

# HITL 관리자 판단 API의 응답 데이터
class HITLDecisionResponse(BaseModel):
    job_id: UUID
    action: HITLAction
    status: ReturnJobStatus
    task_id: str
    message: str
