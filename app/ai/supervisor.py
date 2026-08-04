import os
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
import json

from app.schemas.hitl import HITLAction, HITLReasonCode
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
VISION_RETRY_CODES = {"QUALITY_ERROR","VISION_RESULT_CONFLICT","VISION_LOW_CONFIDENCE"}
POLICY_RETRY_CODES = {"UBCI_POLICY_VIOLATION","POLICY_LOW_CONFIDENCE"}
HITL_REASON_CODES = {"VISION_LOW_CONFIDENCE","VISION_UNCLASSIFIED_DEFECT","POLICY_REQUIRES_HITL"}

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
    
    def route(
        node: str,
        reason: str,
    ) -> str:
        log = {
            "event": "SUPERVISOR_ROUTED",
            "book_id": state.get(
                "book_id"
            ),
            "next_agent": node,
            "reason": reason,
            "vision_status": state.get(
                "vision_status"
            ),
            "vision_reason_code": (
                state.get(
                    "vision_reason_code"
                )
            ),
            "is_mint": state.get(
                "is_mint"
            ),
            "defect_count": (
                len(state["defects"])
                if type(
                    state.get("defects")
                ) is list
                else None
            ),
            "ubci_score": state.get(
                "ubci_score"
            ),
            "predicted_grade": state.get(
                "predicted_grade"
            ),
            "reason_code": state.get(
                "reason_code"
            ),
            "revision_count": state.get(
                "revision_count"
            ),
            "human_feedback": state.get(
                "human_feedback"
            ),
        }

        print(
            "[AI_TRACE] "
            + json.dumps(
                log,
                ensure_ascii=False,
                default=str,
            )
        )

        return node

    raw_revision_count = state.get(
        "revision_count",
        0,
    )

    if (
        type(raw_revision_count) is not int
        or raw_revision_count < 0
    ):
        return route(
            "human_node",
            "잘못된 revision_count",
        )

    revision_count = raw_revision_count
    reason_code = state.get(
        "reason_code"
    )
    vision_status = state.get(
        "vision_status"
    )
    vision_reason_code = state.get(
        "vision_reason_code"
    )
    human_feedback = state.get(
        "human_feedback"
    )


    # 관리자 입력을 가장 먼저 처리
    if human_feedback is not None:
        if human_feedback == "RE_CHECK":
            return route(
                "vision_agent",
                "관리자 재검수·재촬영 요청",
            )

        if (
            human_feedback
            == "APPROVE_NORMAL"
        ):
            return route(
                "report_agent",
                "관리자 정상 승인",
            )

        if (
            human_feedback
            == "APPROVE_DOWNGRADE"
        ):
            if state.get(
                "target_grade"
            ) not in {"A", "B"}:
                return route(
                    "human_node",
                    "하향 승인에는 A/B "
                    "target_grade가 필요함",
                )

            primary_reason_code = (
                state.get(
                    "primary_reason_code"
                )
            )

            if (
                type(primary_reason_code)
                is not str
                or not primary_reason_code.strip()
            ):
                return route(
                    "human_node",
                    "하향 승인에는 "
                    "primary_reason_code가 필요함",
                )

            return route(
                "report_agent",
                "관리자 등급 하향 승인",
            )

        if human_feedback in {
            "REJECT_RETURN",
            "REJECT_DISCARD",
        }:
            primary_reason_code = (
                state.get(
                    "primary_reason_code"
                )
            )

            if (
                type(primary_reason_code)
                is not str
                or not primary_reason_code.strip()
            ):
                return route(
                    "human_node",
                    "반려에는 "
                    "primary_reason_code가 필요함",
                )

            return route(
                "report_agent",
                f"관리자 반려: "
                f"{human_feedback}",
            )

        return route(
            "human_node",
            "허용되지 않은 관리자 입력",
        )

    # Vision이 관리자 검토 또는 실패를 명시한 경우 즉시 HITL 이관
    # is_mint=None은 REVIEW_REQUIRED 상태에서 정상적인 값이므로
    # Vision 출력 누락으로 판단하지 않음
    if vision_status in {
        "REVIEW_REQUIRED",
        "FAILED",
    }:
        return route(
            "human_node",
            vision_reason_code or vision_status,
        )

    # 정의되지 않은 Vision 상태 차단
    if vision_status not in {
        None,
        "COMPLETED",
    }:
        return route(
            "human_node",
            "허용되지 않은 vision_status: "
            f"{vision_status}",
        )

    # Vision 외 에이전트의 무한 재처리 방지
    if revision_count >= MAX_REVISIONS:
        return route(
            "human_node",
            "최대 재시도 횟수 도달",
        )

    # 최초 실행 또는 불완전한 COMPLETED 결과 검사
    vision_output_missing = (
        state.get("is_mint") is None
        or state.get("defects") is None
        or state.get("vision_confidence") is None
        or (
            vision_status is not None
            and (
                state.get("image_quality_ok") is None
                or state.get("yolo_model_manifest") is None
                or state.get("raw_yolo_detections") is None
                or state.get("ensemble_candidates") is None
                or state.get("reviewed_candidates") is None
                or state.get("rejected_candidates") is None
                or state.get("uncertain_candidates") is None
            )
        )
    )

    if vision_output_missing:
        return route(
            "vision_agent",
            "Vision 출력 없음",
        )

    if reason_code in HITL_REASON_CODES:
        return route(
            "human_node",
            reason_code,
        )

    if reason_code in VISION_RETRY_CODES:
        return route(
            "vision_agent",
            reason_code,
        )

    if reason_code in POLICY_RETRY_CODES:
        return route(
            "policy_agent",
            reason_code,
        )

    # 재촬영 결과의 관리자 재확인
    if (
        reason_code == "OK"
        and state.get("primary_reason_code")
        is not None
    ):
        return route(
            "human_node",
            "재촬영 결과 관리자 재확인 필요",
        )

    # Policy의 필수 출력 검사
    policy_output_complete = (
        type(state.get("is_mint"))
        is bool

        and type(state.get("ubci_score"))
        in (int, float)

        and 0
        <= state["ubci_score"]
        <= 100

        and state.get("predicted_grade")
        in {"S", "A", "B", "REJECT"}

        and type(
            state.get("score_breakdown")
        ) is list

                and (
            state.get("fatal_defect_detected") is None
            or type(
                state.get("fatal_defect_detected")
            ) is bool
        )

        and (
            state.get("grade_reason_code") is None
            or (
                type(
                    state.get("grade_reason_code")
                ) is str
                and bool(
                    state["grade_reason_code"].strip()
                )
            )
        )

        and type(
            state.get("rule_reference")
        ) is str

        and bool(
            state[
                "rule_reference"
            ].strip()
        )

        and type(
            state.get(
                "policy_confidence"
            )
        ) in (int, float)

        and 0
        <= state["policy_confidence"]
        <= 1
    )

    # 최초 Vision MINT 결과의 Fast-track
    initial_mint_fast_track = (
        state.get("is_mint") is True
        and state.get("defects") == []
        and state.get("ubci_score") is None
        and state.get("predicted_grade") is None
        and state.get("rule_reference") is None
        and state.get("policy_confidence") is None
        and state.get(
            "primary_reason_code"
        ) is None
    )

    if initial_mint_fast_track:
        return route(
            "auto_refund_agent",
            "최초 Vision MINT Fast-track",
        )

    # Vision 뒤에는 반드시 Policy
    if not policy_output_complete:
        return route(
            "policy_agent",
            "Policy 출력 없음 또는 불완전",
        )

    is_mint = state["is_mint"]
    defects = state["defects"]

    if is_mint is True and defects:
        return route(
            "human_node",
            "MINT인데 결함이 존재함",
        )

    if (
        is_mint is False
        and not defects
    ):
        return route(
            "human_node",
            "비정상인데 결함이 없음",
        )

    if reason_code == "OK":
        return route(
            "report_agent",
            "Critic 검증 통과",
        )

    if reason_code is not None:
        return route(
            "human_node",
            "처리할 수 없는 Reason Code: "
            f"{reason_code}",
        )

    return route(
        "critic_agent",
        "Policy 계산 후 교차 검증 필요",
    )


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

# 전역 그래프 인스턴스
try:
    app_graph = build_supervisor_graph()
except NotImplementedError:
    app_graph = None


TARGET_GRADE_ALIASES = {
    "A": "A",
    "EXCELLENT": "A",
    "B": "B",
    "NORMAL": "B",
}

def resume_hitl(
    thread_id: str,
    human_feedback: str,
    primary_reason_code: str,
    target_grade: str | None = None,
) -> WMSInspectionState:
    """중단된 체크포인트에 관리자 결정을 반영."""

    if app_graph is None:
        raise RuntimeError(
            "Supervisor 그래프가 생성되지 않았습니다."
        )

    if not isinstance(thread_id, str) or not thread_id.strip():
        raise ValueError("thread_id가 필요합니다.")

    raw_decision = getattr(
        human_feedback,
        "value",
        human_feedback,
    )
    raw_reason = getattr(
        primary_reason_code,
        "value",
        primary_reason_code,
    )
    raw_target_grade = getattr(
        target_grade,
        "value",
        target_grade,
    )

    try:
        decision = HITLAction(raw_decision).value
    except (TypeError, ValueError):
        raise ValueError(
            f"허용되지 않은 관리자 결정: {raw_decision!r}"
        ) from None

    try:
        reason = HITLReasonCode(raw_reason).value
    except (TypeError, ValueError):
        raise ValueError(
            f"허용되지 않은 관리자 사유: {raw_reason!r}"
        ) from None

    normalized_target_grade = (
        TARGET_GRADE_ALIASES.get(raw_target_grade)
        if raw_target_grade is not None
        else None
    )

    if (
        decision == "APPROVE_DOWNGRADE"
        and normalized_target_grade is None
    ):
        raise ValueError(
            "APPROVE_DOWNGRADE에는 A/B 또는 "
            "EXCELLENT/NORMAL target_grade가 필요합니다."
        )

    if (
        decision != "APPROVE_DOWNGRADE"
        and raw_target_grade is not None
    ):
        raise ValueError(
            "target_grade는 APPROVE_DOWNGRADE에서만 사용합니다."
        )

    config = {
        "configurable": {
            "thread_id": thread_id,
        }
    }
    snapshot = app_graph.get_state(config)

    if "human_node" not in snapshot.next:
        raise ValueError(
            "HITL Pause 상태가 아니거나 체크포인트가 없습니다."
        )

    update = {
        "human_feedback": decision,
        "primary_reason_code": reason,
        "target_grade": normalized_target_grade,
        "revision_count": 0,
        "final_grade": None,
        "final_report": None,
    }

    if decision == "RE_CHECK":
        update.update({
            "is_mint": None,
            "defects": None,
            "vision_confidence": None,
            "ubci_score": None,
            "predicted_grade": None,
            "score_breakdown": None,
            "rule_reference": None,
            "policy_confidence": None,
            "reason_code": None,
            "repair_directive": "관리자 재촬영 요청",
            "overall_confidence": None,
        })

    app_graph.update_state(
        config,
        update,
        as_node="human_node",
    )

    return app_graph.invoke(
        None,
        config=config,
    )
