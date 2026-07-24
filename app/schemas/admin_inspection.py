from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.wms import ReturnJobStatus


FinalGrade = Literal[
    "MINT",
    "EXCELLENT",
    "GOOD",
    "NORMAL",
    "REJECT",
]

# snake_case 필드명을 camelCase 응답 필드로 변환한다.
def to_camel(field_name: str) -> str:
    parts = field_name.split("_")
    return parts[0] + "".join(
        word.capitalize() for word in parts[1:]
    )

class AgentLogStep(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    step_order: int
    agent_name: str
    execution_status: str
    result_summary: str
    reasoning: str | None = None
    reason_code: str | None = None


class InspectionHistoryRow(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    id: UUID
    book_id: UUID
    book_title: str

    # TODO: UBCI 등급 산정 로직 확정 후 실제 저장값과 연동
    final_grade: FinalGrade | None = None

    # TODO: Fast Track 여부가 agent_logs에 명시적으로 저장되면 연동
    is_fast_track: bool = False

    status: ReturnJobStatus
    ubci_score: float | None = None
    final_report: str | None = None

    reason_codes: list[str] = Field(default_factory=list)

    # 프론트 검수 상세 모달 및 Export 데이터 지원용 Agent 실행 이력
    steps: list[AgentLogStep] = Field(
        default_factory=list,
    )

    inspected_at: datetime
    updated_at: datetime


class InspectionBookDetail(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    id: UUID
    title: str
    isbn: str | None = None


class InspectionAIResult(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    decision: str | None = None
    reason_code: str | None = None
    defects: list[dict[str, Any]] = Field(default_factory=list)
    revision_count: int = 0
    repair_directive: str | None = None


class HITLHistoryItem(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    action: str | None = None
    reviewer_reason_code: str | None = None
    target_grade: str | None = None
    comment: str | None = None
    reviewer_id: UUID | None = None
    reviewer_employee_id: str | None = None
    reviewed_at: datetime | None = None
    task_id: str | None = None


class InspectionErrorDetail(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    type: str | None = None
    message: str | None = None
    task_id: str | None = None
    failed_at: datetime | None = None




class InspectionDetailResponse(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    id: UUID
    book: InspectionBookDetail

    status: ReturnJobStatus
    mode: str

    final_grade: FinalGrade | None = None
    is_fast_track: bool = False

    ubci_score: int | None = None
    final_report: str | None = None

    original_image_urls: list[str] = Field(default_factory=list)

    ai_result: InspectionAIResult
    hitl: dict[str, Any] = Field(default_factory=dict)
    hitl_history: list[HITLHistoryItem] = Field(default_factory=list)

    error: InspectionErrorDetail | None = None
    wms_error: InspectionErrorDetail | None = None

    # TODO: Agent별 로그 저장 구조 확정 후 연동
    steps: list[AgentLogStep] = Field(default_factory=list)

    inspected_at: datetime
    updated_at: datetime