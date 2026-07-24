from typing import Annotated, List, Literal, Optional, TypedDict
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage

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

HumanFeedback = Literal["APPROVE_NORMAL","APPROVE_DOWNGRADE", "REJECT_RETURN","REJECT_DISCARD", "RE_CHECK"]
Grade = Literal["S","A","B","REJECT"]
TargetGrade = Literal["A", "B"]


class WMSInspectionState(TypedDict, total=False):
    """
    LangGraph 에이전트 간 메모리를 공유하기 위한 전역 상태(State) 객체.
    미니프로젝트 3차의 LLMOps 기법을 도입하여 상태를 명확히 제어합니다.
    """
    messages: Annotated[List[BaseMessage], add_messages]
    
    # 1. Vision Agent (1차 판독)
    is_mint: Optional[bool]           # 정상품(새 책) 여부 -> True일 경우 Fast-track 트리거
    defects: Optional[list[dict]]     # 결함 내역 (BBox 등)
    image_quality_ok: Optional[bool]  # 사진을 판독할 수 있는지 여부
    vision_confidence: Optional[float] #vision 전체 판독 신뢰도, 0~1 범위

    # 2. Policy Agent (UBCI 대조)
    ubci_score: Optional[float]         # 훼손도 기반 차감 점수 (100점 만점)
    predicted_grade: Optional[Grade]  # UBCI 점수로 Policy가 산정한 최초 등급 
    score_breakdown: Optional[list[dict]]   # 결함별 심각도와 감점 내역
    fatal_defect_detected: Optional[bool]   # 즉시 반려 결함 존재 여부
    grade_reason_code: Optional[str]        # 자동 등급 산정의 대표 사유
    rule_reference: Optional[str]    # 점수와 등급 산정에 사용한 정책 문서 근거
    policy_confidence: Optional[float] # 정책 검색 및 계산 신뢰도, 0~1 범위
    
    # 3. Critic Agent (교차 검증 및 환각 방어)
    reason_code: Optional[ReasonCode] # 검증 결과 코드 (OK면 통과)
    repair_directive: Optional[str]   # Policy가 다시 계산해야 할 때 주는 수정 지시서
    revision_count: int               # 무한 루프(핑퐁) 방지용 카운터
    overall_confidence: Optional[float] # Vision과 Policy를 종합한 최종 AI 신뢰도

    # 4. Human-In-The-Loop (수동 개입용 - MemorySaver 연동)
    human_feedback: Optional[HumanFeedback]  # 관리자 입력값
    primary_reason_code: Optional[PrimaryReasonCode]  # 관리자가 선택한 감가·오탐·시스템 사유
    target_grade: Optional[TargetGrade]      # 등급 하향 승인 시 관리자가 확정한 A/B 등급
    
    # 5. Output
    final_grade: Optional[Grade]            # 최종 확정된 등급
    final_report: Optional[str]       # 소비자 제공용 사유서 (JSON 문자열)
