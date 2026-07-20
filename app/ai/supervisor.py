import os
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from .state import WMSInspectionState
from .agents import (
    MIN_VISION_CONFIDENCE,
    vision_agent,
    policy_agent,
    critic_agent,
    human_node,
    auto_refund_agent,
    report_agent
)


MAX_REVISIONS = 2
VISION_RETRY_CODES = {"QUALITY_ERROR","BBOX_MISMATCH","VISION_LOW_CONFIDENCE"}
POLICY_RETRY_CODES = {"UBCI_POLICY_VIOLATION","POLICY_LOW_CONFIDENCE"}

# LangSmith Tracing 활성화 (LLMOps)
#os.environ["LANGSMITH_TRACING"] = "true"
#os.environ["LANGSMITH_PROJECT"] = "WMS_AI_Project"
#os.environ["LANGSMITH_ENDPOINT"] = "https://api.smith.langchain.com"

def route_from_supervisor(state: WMSInspectionState) -> str:
    """
    Supervisor Agent의 동적 라우팅 판단 로직 (Star Topology)
    TODO: 아래 주석의 조건에 맞게 각 에이전트로 이동하도록 라우팅 함수를 구현하세요.
    """
    # 1. 안전장치: 무한 루프 방지 (Max Retries가 2 이상이면 human_node(HITL 수동 개입) 반환)
    # 2. 초기 상태: 결함 판독이 아직 안 되었으면 vision_agent 반환
    # 3. [Fast-track]: is_mint가 True이면 auto_refund_agent 반환
    # 4. 결함이 있는데 UBCI 산정이 아직 안 되었으면 policy_agent 반환
    # 5. Critic 검증이 아직 안 됨 (또는 통과 못함) -> reason_code 검토 로직
    #    - 검증 실패 시 policy_agent 재처리 또는 human_node 에스컬레이션 구현
    # 6. Critic 검증 완벽히 통과 시 report_agent 반환
    
    revision_count = state.get("revision_count", 0)

    if type(revision_count) is not int or revision_count < 0:
        return _route("human_node", "잘못된 revision_count")

    if revision_count >= MAX_REVISIONS:
        return _route("human_node", "최대 재시도 횟수 도달")

    reason_code = state.get("reason_code")
    human_feedback = state.get("human_feedback")

    # 관리자 입력은 반드시 human_node에서 한 번 처리된 뒤 결과에 반영합니다.
    if human_feedback is not None:
        if human_feedback == "APPROVE" and reason_code == "OK":
            return _route("report_agent", "관리자 승인")
        if human_feedback == "REJECT" and reason_code is not None:
            return _route("report_agent", "관리자 반려")
        return _route("human_node", "관리자 입력 처리 필요")

    if (
        state.get("is_mint") is None
        or state.get("defects") is None
        or state.get("vision_confidence") is None
    ):
        return _route("vision_agent", "Vision 출력 없음")

    if reason_code in VISION_RETRY_CODES:
        return _route("vision_agent", reason_code)

    if reason_code in POLICY_RETRY_CODES:
        return _route("policy_agent", reason_code)

    if reason_code == "OK":
        return _route("report_agent", "Critic 검증 통과")

    if reason_code is not None:
        return _route("human_node", f"처리할 수 없는 Reason Code: {reason_code}")

    if state.get("is_mint") is True and not state.get("defects"):
        vision_confidence = state.get("vision_confidence")
        if (
            type(vision_confidence) not in (int, float)
            or not 0 <= vision_confidence <= 1
            or vision_confidence < MIN_VISION_CONFIDENCE
        ):
            return _route("vision_agent", "Fast-track 신뢰도 미달")
        return _route("auto_refund_agent", "MINT Fast-track")

    if (
        state.get("ubci_score") is None
        or not state.get("rule_reference")
        or state.get("policy_confidence") is None
    ):
        return _route("policy_agent", "Policy 출력 없음")

    return _route("critic_agent", "교차 검증 필요")


def _route(node: str, reason: str) -> str:
    print(f"[Supervisor] {node} 선택 - {reason}")
    return node

def supervisor_node(state: WMSInspectionState) -> WMSInspectionState:
    """
    Supervisor 노드 자체는 상태를 변경하지 않고 패스스루 역할을 합니다.
    """
    return {}

def build_supervisor_graph():
    """
    LangGraph Supervisor 파이프라인 (Star Topology) 구성
    TODO: 노드와 엣지(Edge), 조건부 엣지를 연결하여 다이어그램과 동일한 그래프를 만드세요.
    """
    builder = StateGraph(WMSInspectionState)

    # 1. 노드 등록 (add_node)
    builder.add_node("supervisor", supervisor_node)
    builder.add_node("vision_agent", vision_agent)
    builder.add_node("policy_agent", policy_agent)
    builder.add_node("critic_agent", critic_agent)
    builder.add_node("human_node", human_node)
    builder.add_node("auto_refund_agent", auto_refund_agent)
    builder.add_node("report_agent", report_agent)

    # TODO: 나머지 6개의 에이전트 노드 등록
    
    # 2. 시작 시 무조건 supervisor로 이동 (add_edge)
    builder.add_edge(START, "supervisor")

    # 3. Supervisor 라우팅 엣지 (supervisor -> 각 에이전트)
    # TODO: builder.add_conditional_edges() 구현
    builder.add_conditional_edges(
        "supervisor",
        route_from_supervisor,
         {
        "vision_agent": "vision_agent",
        "policy_agent": "policy_agent",
        "critic_agent": "critic_agent",
        "human_node": "human_node",
        "auto_refund_agent": "auto_refund_agent",
        "report_agent": "report_agent",
        },
    )
    
    # 4. Star Topology: 워커 에이전트 작업 후 다시 supervisor로 반환
    # TODO: 모든 일반 노드의 종료 엣지를 supervisor로 연결
    builder.add_edge("vision_agent","supervisor")
    builder.add_edge("policy_agent", "supervisor")
    builder.add_edge("critic_agent", "supervisor")
    builder.add_edge("human_node", "supervisor")
    
    # 5. End 엣지 (종료)
    # TODO: auto_refund_agent 와 report_agent 를 END 로 연결
    builder.add_edge("auto_refund_agent",END)
    builder.add_edge("report_agent", END)
    
    # 6. MemorySaver 연동 (HITL 중단점)
    memory = MemorySaver()
    graph = builder.compile(checkpointer=memory, interrupt_before=["human_node"])
    return graph

# 전역 그래프 인스턴스 (현재는 NotImplementedError가 발생합니다)
try:
    app_graph = build_supervisor_graph()
except NotImplementedError:
    app_graph = None
