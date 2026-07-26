from typing import Annotated, List, Literal, Optional, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

# Critic 에이전트가 뱉어낼 수 있는 명시적 에러 코드 (Reason Code)
ReasonCode = Literal[
    "UBCI_POLICY_VIOLATION",
    "VISION_RESULT_CONFLICT",
    "QUALITY_ERROR",
    "VISION_LOW_CONFIDENCE",
    "VISION_UNCLASSIFIED_DEFECT",
    "POLICY_LOW_CONFIDENCE",
    "POLICY_REQUIRES_HITL",
    "OK",
]

PrimaryReasonCode = Literal[
    "FP_SHADOW", "FP_COVER_PATTERN", "FP_GLARE",
    "FP_BOOK_BAND", "FP_DUST", "FP_OTHER",
    "DMG_EXT_WET", "DMG_EXT_CRUSH", "DMG_EXT_TEAR", "DMG_EXT_BOX",
    "DMG_INT_BINDING", "DMG_INT_STAIN",
    "DMG_INT_DISCOLOR", "DMG_INT_BARCODE",
    "SYS_BLURRY", "SYS_WRONG_ITEM",
    "SYS_MISSING_PARTS", "SYS_OTHER",
]

HumanFeedback = Literal[
    "APPROVE_NORMAL",
    "APPROVE_DOWNGRADE",
    "REJECT_RETURN",
    "REJECT_DISCARD",
    "RE_CHECK",
]
Grade = Literal["S", "A", "B", "REJECT"]
TargetGrade = Literal["A", "B"]


class WMSInspectionState(TypedDict, total=False):
    """
    LangGraph 전체 노드가 공유하는 검수 상태입니다.

    ``total=False``이므로 각 Agent는 자신이 담당하는 필드만 갱신할 수
    있습니다. messages에는 ``add_messages`` reducer가 적용되어 Agent의
    실행 이력이 기존 메시지 뒤에 누적됩니다.
    """
    messages: Annotated[List[BaseMessage], add_messages]

    # 요청 추적 정보: Worker/DB 작업과 LangGraph 실행을 연결합니다.
    tenant_id: str
    book_id: str
    mode: str

    # 1. Vision Agent (1차 판독)
    image_paths: list[str]  # 현재는 Vision이 직접 열 수 있는 로컬 경로
    is_mint: Optional[bool]  # 결함 없음 여부. True이면 Fast-track 후보
    defects: Optional[list[dict]]  # VLM이 승인한 결함과 YOLO BBox
    image_quality_ok: Optional[bool]  # 모든 입력 사진의 판독 가능 여부
    vision_confidence: Optional[float]  # 전체 Vision 판독 신뢰도, 0~1

    # 2. Policy Agent (UBCI 대조)
    ubci_score: Optional[float]  # 훼손도 기반 차감 점수, 0~100
    predicted_grade: Optional[Grade]  # Policy가 산정한 최초 등급
    score_breakdown: Optional[list[dict]]  # 결함별 심각도와 감점 내역
    fatal_defect_detected: Optional[bool]  # 즉시 반려 결함 존재 여부
    grade_reason_code: Optional[str]  # 자동 등급 산정의 대표 사유
    rule_reference: Optional[str]  # 점수 산정에 사용한 정책 근거
    policy_confidence: Optional[float]  # 정책 검색·계산 신뢰도, 0~1

    # 3. Critic Agent (교차 검증 및 환각 방어)
    reason_code: Optional[ReasonCode]  # 검증 결과 코드. OK이면 자동 통과
    repair_directive: Optional[str]  # 재처리 또는 관리자 확인 지시
    revision_count: int  # Agent 재시도 횟수. 무한 루프 방지용
    overall_confidence: Optional[float]  # Vision/Policy 중 낮은 신뢰도

    # 4. Human-In-The-Loop (수동 개입용 - MemorySaver 연동)
    human_feedback: Optional[HumanFeedback]  # 관리자 결정
    primary_reason_code: Optional[PrimaryReasonCode]  # 관리자 사유
    target_grade: Optional[TargetGrade]  # 하향 승인 시 확정 A/B 등급

    # 5. Output
    final_grade: Optional[Grade]  # AI 또는 관리자가 최종 확정한 등급
    final_report: Optional[str]  # 후속 시스템에 제공하는 JSON 문자열
