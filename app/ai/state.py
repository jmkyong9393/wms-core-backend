from typing import Annotated, List, Literal, Optional, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

# Policy 또는 Critic 검증 후 Supervisor가 처리할 상태코드
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

# Vision Agent 전용 상태 코드
VisionStatus = Literal[
    "COMPLETED",
    "REVIEW_REQUIRED",
    "FAILED",
]

# Vision이 관리자 확인 또는 실패 사유를 표시하는 코드 
VisionReasonCode = Literal[
    "QUALITY_ERROR",
    "VISION_LOW_CONFIDENCE",
    "VISION_UNCLASSIFIED_DEFECT",
]

# HITL 관리자 사유코드 
PrimaryReasonCode = Literal[
    # AI 오탐: 정상 도서를 결함으로 잘못 판단
    "FP_SHADOW",
    "FP_COVER_PATTERN",
    "FP_GLARE",
    "FP_BOOK_BAND",
    "FP_DUST",
    "FP_OTHER",
    # 외부 파손
    "DMG_EXT_WET",
    "DMG_EXT_CRUSH",
    "DMG_EXT_TEAR",
    "DMG_EXT_BOX",
    "DMG_EXT_OTHER",
    #내부 결함
    "DMG_INT_BINDING",
    "DMG_INT_STAIN",
    "DMG_INT_DISCOLOR",
    "DMG_INT_BARCODE",
    "DMG_INT_OTHER",
    #재촬영 및 시스템 예외
    "SYS_BLURRY",
    "SYS_WRONG_ITEM",
    "SYS_MISSING_PARTS",
    "SYS_OTHER",
]

# HITL 관리자 결정 코드
HumanFeedback = Literal[
    "APPROVE_NORMAL",
    "APPROVE_DOWNGRADE",
    "REJECT_RETURN",
    "REJECT_DISCARD",
    "RE_CHECK",
]

#등급 코드
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

    # 요청 추적 정보
    tenant_id: str # 고객사 또는 물류센터 식별 값
    book_id: str # 검수 중인 단품 도서 또는 LPN 식별값
    mode: str # 실행모드 예: INSPECTION< RE_CHECK
    image_paths: list[str] # Vision Agent가 검사할 실제 이미지 경로 목록

    # YOLO 앙상블 원본 결과
    yolo_model_manifest: Optional[list[dict]] # YOLO 모델 이름, 경로, 역할, 클래스 목록
    raw_yolo_detections: Optional[list[dict]] # 각 YOLO 모델이 출력한 가공 전 탐지 결과
    ensemble_candidates: Optional[list[dict]] # 여러 YOLO 모델의 겹치는 BBOX를 병합한 후보 목록, 이후보가 VLM 2차 검토 입력
    
    # 1. Vision Agent (2차 검토)
    reviewed_candidates: Optional[list[dict]] # CONFIRMED ,REJECTED, UNCERTAIN 결과를 모두 포함
    rejected_candidates: Optional[list[dict]] # 오탐으로 판단한 후보
    uncetain_candidates: Optional[list[dict]] # VLM도 확정하지 못해 HITL로 넘겨야하는 후보
    defects: Optional[list[dict]] # VLM이 최종 승인한 실제 결함 목록
    image_quality_ok: Optional[bool] # 입력 사진 전체가 판독 가능한 품질인지 표시
    vision_confidence: Optional[float]  # 전체 Vision 판독 신뢰도, 0~1
    vision_status: Optional[VisionStatus] # Vision 실행 상태
    vision_reason_code: Optional[VisionReasonCode]  # Vision이 REVIEW_REQUIRED 또는 FAILED가 된 사유

    # 2. Policy Agent (UBCI 대조)
    is_mint: Optional[bool]
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
