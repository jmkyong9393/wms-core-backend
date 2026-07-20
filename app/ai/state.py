from typing import Annotated, List, Literal, Optional, TypedDict
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage

# Critic 에이전트가 뱉어낼 수 있는 명시적 에러 코드 (Reason Code)
ReasonCode = Literal[
    "UBCI_POLICY_VIOLATION",
    "BBOX_MISMATCH",
    "QUALITY_ERROR",
    "VISION_LOW_CONFIDENCE",
    "POLICY_LOW_CONFIDENCE",
    "OK",
]
HumanFeedback = Literal["APPROVE", "REJECT", "RECALCULATE"]


class WMSInspectionState(TypedDict, total=False):
    """
    LangGraph 에이전트 간 메모리를 공유하기 위한 전역 상태(State) 객체.
    미니프로젝트 3차의 LLMOps 기법을 도입하여 상태를 명확히 제어합니다.
    """
    messages: Annotated[List[BaseMessage], add_messages]
    
    # 1. Vision Agent (1차 판독)
    is_mint: Optional[bool]           # 정상품(새 책) 여부 -> True일 경우 Fast-track 트리거
    defects: Optional[list[dict]]     # 결함 내역 (BBox 등)
    vision_confidence: Optional[float]
    
    # 2. Policy Agent (UBCI 대조)
    ubci_score: Optional[int]         # 훼손도 기반 차감 점수 (100점 만점)
    rule_reference: Optional[str]
    policy_confidence: Optional[float]
    
    # 3. Critic Agent (교차 검증 및 환각 방어)
    reason_code: Optional[ReasonCode] # 검증 결과 코드 (OK면 통과)
    repair_directive: Optional[str]   # Policy가 다시 계산해야 할 때 주는 수정 지시서
    revision_count: int               # 무한 루프(핑퐁) 방지용 카운터
    overall_confidence: Optional[float]

    # 4. Human-In-The-Loop (수동 개입용 - MemorySaver 연동)
    human_feedback: Optional[HumanFeedback]  # 관리자 입력값
    
    # 5. Output
    final_report: Optional[str]       # 소비자 제공용 사유서 (JSON 문자열)
