from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from app.models.wms import ReturnJobStatus


FinalGrade = Literal[
    "MINT",
    "EXCELLENT",
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
    cover_image_url: str | None = None

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

class InspectionHistoryListResponse(BaseModel):
    """
    관리자 검수 이력 그리드의 서버 페이지네이션 응답.

    items에는 요청한 페이지의 검수 이력만 포함하고,
    total은 현재 필터 조건에 맞는 전체 검수 이력 수다.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    items: list[InspectionHistoryRow] = Field(
        default_factory=list,
        description="현재 페이지의 검수 이력 목록",
    )
    total: int = Field(
        ge=0,
        description="필터 조건에 맞는 전체 검수 이력 수",
    )
    page: int = Field(
        ge=1,
        description="현재 페이지 번호",
    )
    size: int = Field(
        ge=1,
        description="페이지당 조회 건수",
    )
    total_pages: int = Field(
        ge=0,
        serialization_alias="total_pages",
        description="전체 페이지 수",
    )

class HITLBoardItem(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    id: UUID
    book_id: UUID
    book_title: str
    lpn_barcode: str | None = None
    location_barcode: str | None = None

    status: ReturnJobStatus
    ubci_score: float | None = None
    final_grade: FinalGrade | None = None
    reason_codes: list[str] = Field(default_factory=list)

    reviewer_id: UUID | None = None
    reviewer_employee_id: str | None = None
    reviewer_name: str | None = None
    review_started_at: datetime | None = None

    created_at: datetime
    updated_at: datetime


class HITLQueueListResponse(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    items: list[HITLBoardItem] = Field(
        default_factory=list,
    )
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    size: int = Field(ge=1)
    total_pages: int = Field(ge=0)
    has_more: bool

class HITLQueueMetricsResponse(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    pending_count: int = Field(
        ge=0,
        description="아직 관리자가 가져가지 않은 HITL 검토 대기 건수",
    )
    today_completed_count: int = Field(
        ge=0,
        description="오늘 최종 승인 또는 반려 처리된 건수",
    )
    overdue_count: int = Field(
        ge=0,
        description="30분 이상 미처리 상태인 HITL 건수",
    )

class InspectionBookDetail(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    id: UUID
    title: str
    isbn: str | None = None
    cover_image_url: str | None = None

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

class VisionDefect(BaseModel):
    """
    원본 이미지 위 결함 영역을 표시하기 위한 Vision 결과.
    기존 defects의 확장 형식이며, BBOX가 완전한 데이터만 반환한다.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    image_index: int = Field(ge=0)
    image_view: Literal["FRONT", "BACK", "INSIDE"]
    image_url: str = Field(min_length=1)

    # 기존 Policy/Critic 호환용
    type: str = Field(min_length=1)
    ratio: float = Field(ge=0, le=100)

    # 화면 표시용
    defect_type: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    yolo_confidence: float = Field(ge=0, le=1)
    bbox: list[float] = Field(min_length=4, max_length=4)
    coordinate_space: Literal["ORIGINAL_IMAGE_NORMALIZED"]

    @model_validator(mode="after")
    def validate_original_image_bbox(self):
        x1, y1, x2, y2 = self.bbox

        if not (
            0 <= x1 < x2 <= 1
            and 0 <= y1 < y2 <= 1
        ):
            raise ValueError(
                "bbox must be [x1, y1, x2, y2] in "
                "ORIGINAL_IMAGE_NORMALIZED coordinates"
            )

        return self

class YoloCandidate(BaseModel):
    """
    YOLO가 제안한 모든 결함 후보.
    VLM이 반려하거나 보류한 후보도 포함한다.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    candidate_id: int = Field(ge=0)
    image_index: int = Field(ge=0)
    image_view: Literal["FRONT", "BACK", "INNER"]
    image_url: str = Field(min_length=1)

    defect_type: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    bbox: list[float] = Field(min_length=4, max_length=4)
    coordinate_space: Literal["ORIGINAL_IMAGE_NORMALIZED"]

    source_model: str | None = None
    review_decision: Literal[
        "CONFIRMED",
        "REJECTED",
        "UNCERTAIN",
    ] = "UNCERTAIN"
    reject_reason: str | None = None

    @model_validator(mode="after")
    def validate_original_image_bbox(self):
        x1, y1, x2, y2 = self.bbox

        if not (
            0 <= x1 < x2 <= 1
            and 0 <= y1 < y2 <= 1
        ):
            raise ValueError(
                "bbox must be [x1, y1, x2, y2] in "
                "ORIGINAL_IMAGE_NORMALIZED coordinates"
            )

        return self


class ConfirmedDefect(BaseModel):
    """
    Vision Agent가 실제 결함으로 확정한 후보.
    UBCI 및 품질보증서 계산에 사용하는 최종 결함 목록이다.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    candidate_id: int = Field(ge=0)
    image_index: int = Field(ge=0)
    image_view: Literal["FRONT", "BACK", "INNER"]
    image_url: str = Field(min_length=1)

    defect_type: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    bbox: list[float] = Field(min_length=4, max_length=4)
    coordinate_space: Literal["ORIGINAL_IMAGE_NORMALIZED"]

    @model_validator(mode="after")
    def validate_original_image_bbox(self):
        x1, y1, x2, y2 = self.bbox

        if not (
            0 <= x1 < x2 <= 1
            and 0 <= y1 < y2 <= 1
        ):
            raise ValueError(
                "bbox must be [x1, y1, x2, y2] in "
                "ORIGINAL_IMAGE_NORMALIZED coordinates"
            )

        return self


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
    lpn_barcode: str | None = None
    label_scan_url: str | None = None

    original_image_urls: list[str] = Field(default_factory=list)

    vision_detections: list[VisionDefect] = Field(
        default_factory=list,
        description=(
            "원본 이미지 기준 정규화 BBOX가 포함된 "
            "Vision 결함 목록"
        ),
    )

    yolo_candidates: list[YoloCandidate] = Field(
        default_factory=list,
        description="YOLO가 제안한 전체 결함 후보 목록",
    )
    confirmed_defects: list[ConfirmedDefect] = Field(
        default_factory=list,
        description="Vision Agent가 최종 확정한 결함 목록",
    )

    ai_result: InspectionAIResult
    hitl: dict[str, Any] = Field(default_factory=dict)
    hitl_history: list[HITLHistoryItem] = Field(default_factory=list)

    error: InspectionErrorDetail | None = None
    wms_error: InspectionErrorDetail | None = None

    # TODO: Agent별 로그 저장 구조 확정 후 연동
    steps: list[AgentLogStep] = Field(default_factory=list)

    inspected_at: datetime
    updated_at: datetime