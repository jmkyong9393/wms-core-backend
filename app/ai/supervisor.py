import logging
import os

# LangSmith 추적을 사용하더라도 검수 이미지 경로와 판정 State 본문은
# 명시적으로 허용하지 않는 한 외부 추적 저장소에 전송하지 않습니다.
os.environ.setdefault("LANGSMITH_HIDE_INPUTS", "true")
os.environ.setdefault("LANGSMITH_HIDE_OUTPUTS", "true")

import json

from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from app.domains.inspections.schemas.hitl import HITLAction, HITLReasonCode

from .agents import (
    IMAGE_VIEWS,
    book_detector_node,
    critic_agent,
    human_node,
    policy_agent,
    report_agent,
    vision_agent,
)
from .state import WMSInspectionState

logger = logging.getLogger(__name__)

MAX_REVISIONS = 2
VISION_RETRY_CODES = {"VISION_RESULT_CONFLICT"}
POLICY_RETRY_CODES = {"UBCI_POLICY_VIOLATION","POLICY_LOW_CONFIDENCE"}
HITL_REASON_CODES = {"VISION_LOW_CONFIDENCE","VISION_UNCLASSIFIED_DEFECT","POLICY_REQUIRES_HITL"}
SYSTEM_FAILURE_CODES = {"QUALITY_ERROR"}


def technical_failure_node(
    state: WMSInspectionState,
) -> WMSInspectionState:
    """사람의 판정으로 해결할 수 없는 기술 실패를 Worker에 전달."""

    detail = (
        state.get("repair_directive")
        or state.get("vision_reason_code")
        or state.get("vision_status")
        or "알 수 없는 AI 파이프라인 오류"
    )
    raise RuntimeError(str(detail))


# LangSmith Tracing 활성화 (LLMOps)
#os.environ["LANGSMITH_TRACING"] = "true"
#os.environ["LANGSMITH_PROJECT"] = "WMS_AI_Project"
#os.environ["LANGSMITH_ENDPOINT"] = "https://api.smith.langchain.com"

def _decide_next_node(
    state: WMSInspectionState,
) -> tuple[str, str]:
    """Supervisor의 지휘 판단 본체 — (다음 노드, 판단 근거)를 반환한다.

    판단은 supervisor_node가 수행해 결정·근거를 state에 기록하고,
    라우팅 함수(route_from_supervisor)는 그 결정을 집행만 한다.
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
    ) -> tuple[str, str]:
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

        logger.info(
            "[AI_TRACE] %s",
            json.dumps(
                log,
                ensure_ascii=False,
                default=str,
            ),
        )

        return node, reason

    raw_revision_count = state.get(
        "revision_count",
        0,
    )

    if (
        type(raw_revision_count) is not int
        or raw_revision_count < 0
    ):
        return route(
            "technical_failure_node",
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
            book_regions = state.get("book_regions")
            if book_regions is None:
                return route(
                    "book_detector",
                    "관리자 재검수·재촬영 요청",
                )
            if (
                type(book_regions) is list
                and len(book_regions) == len(IMAGE_VIEWS)
            ):
                return route(
                    "vision_agent",
                    "재촬영 이미지의 책 영역 탐지 완료",
                )
            return route(
                "technical_failure_node",
                "재촬영 이미지의 책 영역 탐지 실패",
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

    # 판독 커버리지 게이트 — "검수하지 못했다"와 "검수했더니 흠이 없다"는
    # 하위 노드에서 똑같이 defects=[]로 표현되며, 그 둘을 구분할 수 있는 정보
    # (book_regions의 detected 여부)는 전 에이전트 보고를 종합하는 여기에만 모인다.
    # 전 컷이 책 미식별인데 MINT가 나오면 무결점이 아니라 검수 불가이므로 자동 확정을 차단한다.
    book_regions = state.get("book_regions")
    if (
        type(book_regions) is list
        and len(book_regions) > 0
        and all(
            type(region) is dict
            and region.get("detected") is not True
            for region in book_regions
        )
        and state.get("is_mint") is True
    ):
        return route(
            "human_node",
            "판독 커버리지 미달 - 촬영 전 컷이 책 미식별 상태로 결함 0건은 "
            "'무결점'이 아니라 '검수 불가'를 의미함. 자동 확정 차단 후 관리자 이관",
        )

    # Vision이 관리자 검토를 명시한 경우에만 HITL 이관
    # is_mint=None은 REVIEW_REQUIRED 상태에서 정상적인 값이므로
    # Vision 출력 누락으로 판단하지 않음
    if vision_status == "REVIEW_REQUIRED":
        return route(
            "human_node",
            vision_reason_code or vision_status,
        )

    # 파일·모델·외부 API 오류는 사람 판정 대상이 아니므로
    # Worker의 기존 재시도 및 FAILED 저장 경로로 전달
    if vision_status == "FAILED":
        return route(
            "technical_failure_node",
            vision_reason_code or vision_status,
        )

    # 정의되지 않은 Vision 상태 차단
    if vision_status not in {
        None,
        "COMPLETED",
    }:
        return route(
            "technical_failure_node",
            "허용되지 않은 vision_status: "
            f"{vision_status}",
        )

    # Vision 외 에이전트의 무한 재처리 방지. 운영 정책에 따라
    # 두 번의 재처리 이후에는 관리자에게 마지막 증거를 제공합니다.
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
        book_regions = state.get("book_regions")
        if book_regions is None:
            return route(
                "book_detector",
                "책 영역 탐지 결과 없음",
            )

        if (
            type(book_regions) is not list
            or len(book_regions) != len(IMAGE_VIEWS)
        ):
            return route(
                "technical_failure_node",
                state.get("repair_directive")
                or "Book Detector 출력이 불완전함",
            )

        return route(
            "vision_agent",
            "책 영역 탐지 완료 후 Vision 분석 필요",
        )
    if reason_code in HITL_REASON_CODES:
        return route(
            "human_node",
            reason_code,
        )

    if reason_code in SYSTEM_FAILURE_CODES:
        return route(
            "technical_failure_node",
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

        and type(
            state.get("fatal_defect_detected")
        ) is bool

        and type(
            state.get("grade_reason_code")
        ) is str

        and bool(
            state["grade_reason_code"].strip()
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
            "technical_failure_node",
            "MINT인데 결함이 존재함",
        )

    if (
        is_mint is False
        and not defects
    ):
        return route(
            "technical_failure_node",
            "비정상인데 결함이 없음",
        )

    if reason_code == "OK":
        if is_mint is True:
            # MINT 별도 출구(auto_refund_agent)를 제거하고 report_agent로 단일화.
            # 자동 매입 자격은 auto_refund_eligible 플래그로 보존해 워커가 집행한다.
            return route(
                "report_agent",
                "Policy와 Critic을 통과한 MINT — 단일 검증 경로로 보고서 발급",
            )
        return route(
            "report_agent",
            "Critic 검증 통과",
        )

    if reason_code is not None:
        return route(
            "technical_failure_node",
            "처리할 수 없는 Reason Code: "
            f"{reason_code}",
        )

    return route(
        "critic_agent",
        "Policy 계산 후 교차 검증 필요",
    )



def supervisor_node(state: WMSInspectionState) -> WMSInspectionState:
    """Supervisor 중앙 지휘 노드 — 하위 에이전트 보고를 종합해 다음 행동을 결정한다.

    결정(supervisor_decision)과 근거(supervisor_rationale)를 state에 남겨
    관리자 화면과 감사 추적에서 "왜 이 경로로 갔는가"를 재구성할 수 있게 한다.
    """
    node, reason = _decide_next_node(state)

    # MINT 자동 매입 자격: 단일 검증 경로(Policy→Critic)를 전부 통과한 무결함 건만 인정
    auto_refund_eligible = (
        node == "report_agent"
        and state.get("human_feedback") is None
        and state.get("is_mint") is True
        and state.get("reason_code") == "OK"
        and not (state.get("defects") or [])
    )

    return {
        "supervisor_decision": node,
        "supervisor_rationale": reason,
        "auto_refund_eligible": (
            True if auto_refund_eligible
            else state.get("auto_refund_eligible")
        ),
        "messages": [
            AIMessage(
                content=f"[Supervisor] {node} 지휘 - {reason}"
            )
        ],
    }


# supervisor_node의 결정 -> 그래프 노드 매핑 (라우팅 함수는 집행만 한다)
_DECISION_TO_NODE = {
    "book_detector": "book_detector",
    "vision_agent": "vision_agent",
    "policy_agent": "policy_agent",
    "critic_agent": "critic_agent",
    "human_node": "human_node",
    "technical_failure_node": "technical_failure_node",
    "report_agent": "report_agent",
}


def route_from_supervisor(state: WMSInspectionState) -> str:
    """supervisor_node가 내린 결정을 집행하는 매핑 함수.

    결정이 비어 있으면(개편 이전에 저장된 체크포인트 재개 등) 판단 본체를
    직접 호출해 폴백한다 — 구 체크포인트가 technical_failure로 오배송되지 않게.
    """
    decision = state.get("supervisor_decision")
    if decision is None:
        decision, _ = _decide_next_node(state)
    return _DECISION_TO_NODE.get(decision, "technical_failure_node")

def build_supervisor_graph():
    """
    LangGraph Supervisor 파이프라인 (Star Topology) 구성
    TODO: 노드와 엣지(Edge), 조건부 엣지를 연결하여 다이어그램과 동일한 그래프를 만드세요.
    """
    builder = StateGraph(WMSInspectionState)

    # 1. 노드 등록 (add_node)
    # 계측 래퍼로 감싼다 — 구간 지연과 LLM 토큰을 노드 단위로 수집한다.
    # 노드 구현은 그대로 두고 등록 시점에만 감싸므로 파이프라인 구조·모델 배정은 불변이다.
    from app.ai.instrumentation import instrument

    builder.add_node("book_detector", instrument("book_detector", book_detector_node))
    builder.add_node("supervisor", instrument("supervisor", supervisor_node))
    builder.add_node("vision_agent", instrument("vision_agent", vision_agent))
    builder.add_node("policy_agent", instrument("policy_agent", policy_agent))
    builder.add_node("critic_agent", instrument("critic_agent", critic_agent))
    builder.add_node("human_node", instrument("human_node", human_node))
    builder.add_node(
        "technical_failure_node",
        technical_failure_node,
    )
    builder.add_node("report_agent", instrument("report_agent", report_agent))

    # TODO: 나머지 6개의 에이전트 노드 등록

    # 2. 시작 시 무조건 supervisor로 이동 (add_edge)
    builder.add_edge(START, "supervisor")

    # 3. Supervisor 라우팅 엣지 (supervisor -> 각 에이전트)
    # TODO: builder.add_conditional_edges() 구현
    builder.add_conditional_edges(
        "supervisor",
        route_from_supervisor,
         {
        "book_detector": "book_detector",
        "vision_agent": "vision_agent",
        "policy_agent": "policy_agent",
        "critic_agent": "critic_agent",
        "human_node": "human_node",
        "technical_failure_node": "technical_failure_node",
        "report_agent": "report_agent",
        },
    )

    # 4. Star Topology: 워커 에이전트 작업 후 다시 supervisor로 반환
    # TODO: 모든 일반 노드의 종료 엣지를 supervisor로 연결
    builder.add_edge("book_detector", "supervisor")
    builder.add_edge("vision_agent","supervisor")
    builder.add_edge("policy_agent", "supervisor")
    builder.add_edge("critic_agent", "supervisor")
    builder.add_edge("human_node", "supervisor")
    builder.add_edge("technical_failure_node", END)

    # 5. End 엣지 (종료) — MINT 포함 전 건이 report_agent 단일 출구로 종료
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

    if (
        not isinstance(thread_id, str)
        or not thread_id.strip()
        or len(thread_id) > 500
    ):
        raise ValueError(
            "thread_id는 1~500자의 문자열이어야 합니다."
        )

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
            "book_regions": None,
            "yolo_model_manifest": None,
            "raw_yolo_detections": None,
            "ensemble_candidates": None,
            "reviewed_candidates": None,
            "rejected_candidates": None,
            "uncertain_candidates": None,
            "image_quality_ok": None,
            "vision_status": None,
            "vision_reason_code": None,
            "missed_defect_suspected": None,
            "vision_observations": None,
            "is_mint": None,
            "defects": None,
            "vision_confidence": None,
            "ubci_score": None,
            "provisional_ubci_score": None,
            "predicted_grade": None,
            "score_breakdown": None,
            "provisional_score_breakdown": None,
            "fatal_defect_detected": None,
            "grade_reason_code": None,
            "rule_reference": None,
            "policy_confidence": None,
            "policy_evidence": None,
            "policy_rag_status": None,
            "policy_rag_domains": None,
            "critic_rag_used": None,
            "critic_retrieved_case_ids": None,
            "critic_retrieval_scores": None,
            "critic_retrieval_count": None,
            "critic_decision_source": None,
            "critic_explanation": None,
            "critic_rag_confidence": None,
            "critic_prompt_version": None,
            "reason_code": None,
            "repair_directive": "관리자 재촬영 요청",
            "overall_confidence": None,
            "supervisor_decision": None,
            "supervisor_rationale": None,
            "auto_refund_eligible": None,
        })

    app_graph.update_state(
        config,
        update,
        as_node="human_node",
    )

    final_state = app_graph.invoke(
        None,
        config=config,
    )

    if decision != "RE_CHECK":
        try:
            from .rag.critic_cases import (
                upsert_authoritative_hitl_case,
            )

            stored_case_id = (
                upsert_authoritative_hitl_case(
                    final_state,
                    case_id=f"hitl-{thread_id}",
                    final_decision=decision,
                    primary_reason_code=reason,
                    target_grade=normalized_target_grade,
                )
            )
            if stored_case_id:
                logger.info(
                    "[Critic RAG] HITL 권위 판례 저장 - %s",
                    stored_case_id,
                )
        except Exception as error:
            logger.warning(
                "[Critic RAG] HITL 판례 저장 실패 - %s",
                type(error).__name__,
            )

    return final_state
