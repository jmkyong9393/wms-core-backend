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

# UBCI 등급 코드
Grade = Literal[
    "S",
    "A",
    "B",
    "REJECT",
]


class WMSInspectionState(TypedDict, total=False):
    """
    LangGraph 에이전트 간 메모리를 공유하기 위한 전역 상태(State) 객체.
    미니프로젝트 3차의 LLMOps 기법을 도입하여 상태를 명확히 제어합니다.
    """
    messages: Annotated[List[BaseMessage], add_messages]
    tenant_id: str  # 고객사 또는 물류센터 식별값
    
    # 1. Vision Agent (1차 판독)
    is_mint: Optional[bool]           # 정상품(새 책) 여부 -> True일 경우 Fast-track 트리거
    defects: Optional[list[dict]]     # 결함 내역 (BBox 등)
    vision_confidence: Optional[float]
    
    # 2. Policy Agent (UBCI 대조)
    ubci_score: Optional[float]  # 훼손도 기반 차감 점수, 0~100
    predicted_grade: Optional[Grade]  # Policy 예측 등급
    score_breakdown: Optional[list[dict]]  # 결함별 심각도 및 감점 내역
    rule_reference: Optional[str]
    policy_confidence: Optional[float]
    
    # 3. Critic Agent (교차 검증 및 환각 방어)
    reason_code: Optional[ReasonCode] # 검증 결과 코드 (OK면 통과)
    repair_directive: Optional[str]   # Policy가 다시 계산해야 할 때 주는 수정 지시서
    revision_count: int               # 무한 루프(핑퐁) 방지용 카운터
    overall_confidence: Optional[float]
    critic_rag_used: Optional[bool]  # 판례 RAG가 최종 판단에 사용되었는지 여부
    critic_retrieved_case_ids: Optional[List[str]]  # 검색된 유사 판례 ID
    critic_retrieval_scores: Optional[List[float]]  # Chroma 거리 점수. 작을수록 유사
    critic_retrieval_count: Optional[int]  # 검색된 판례 개수
    critic_decision_source: Optional[str]  # RULE_ONLY / RULE_AND_RAG / RULE_FALLBACK
    critic_explanation: Optional[str]  # Critic의 최종 검증 근거
    critic_rag_confidence: Optional[float]  # 판례 기반 판단 신뢰도
    critic_prompt_version: Optional[str]  # 사용한 동적 Few-shot 프롬프트 버전

    # 4. Human-In-The-Loop (수동 개입용 - MemorySaver 연동)
    human_feedback: Optional[HumanFeedback]  # 관리자 입력값
    
    # 5. Output
    final_report: Optional[str]       # 소비자 제공용 사유서 (JSON 문자열)
