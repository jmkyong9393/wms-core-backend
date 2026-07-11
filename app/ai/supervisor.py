import os
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from .state import WMSInspectionState
from .agents import (
    vision_agent,
    policy_agent,
    critic_agent,
    human_node,
    auto_refund_agent,
    report_agent
)

# LangSmith Tracing 활성화 (LLMOps)
os.environ["LANGSMITH_TRACING"] = "true"
os.environ["LANGSMITH_PROJECT"] = "WMS_AI_Project"
os.environ["LANGSMITH_ENDPOINT"] = "https://api.smith.langchain.com"

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
    
    #raise NotImplementedError("Supervisor 라우팅 로직을 구현해주세요.")
    
    if state.get("final_report") is not None:
        return "__end__"
    
    if state.get("revision_count",0) >=2:
        return "human_node"
    
    if state.get("is_mint") is None:
        return "vision_agent"
    
    if state.get("is_mint") is True:
        return "auto_refund_agent"
    
    if state.get("ubci_score") is None:
        return "policy_agent"
    
    if state.get("reson_code") is None:
        return "critic_agent"
    
    if state.get("resonn_code") != "OK":
        return "policy_agent"
    
    return "report_agent"

def supervisor_node(state: WMSInspectionState) -> WMSInspectionState:
    """
    Supervisor 노드 자체는 상태를 변경하지 않고 패스스루 역할을 합니다.
    """
    return state

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
        "__end__": END,
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
