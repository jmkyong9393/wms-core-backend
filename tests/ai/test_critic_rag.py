import json
import os
import uuid

import pytest

from langchain_core.documents import Document
from pydantic import ValidationError

from app.ai import agents
from app.ai import supervisor
from app.ai.rag import critic_cases
from app.ai.rag.critic_cases import (
    CriticCase,
    CriticFewShotDecision,
)


RUN_LIVE = (
    os.getenv("RUN_CRITIC_RAG_LIVE")
    == "1"
)


# 공통 검사 상태
def make_state(**overrides):
    state = {
        "tenant_id": "DEV_TEST",
        "is_mint": False,
        "defects": [
            {
                "type": "WATER_DAMAGE",
                "location": "INNER_PAGE",
                "ratio": 8.2,
                "confidence": 0.93,
            }
        ],
        "vision_confidence": 0.93,
        "ubci_score": 72.5,
        "predicted_grade": "B",
        "score_breakdown": [
            {
                "type": "WATER_DAMAGE",
                "applied_penalty": 27.5,
            }
        ],
        "rule_reference": (
            "UBCI_SPEC_V2.0.0.0"
        ),
        "policy_confidence": 0.91,
        "revision_count": 0,
        "human_feedback": None,
        "final_report": None,
    }
    state.update(overrides)
    return state


# 공통 판례 데이터
def make_case(
    case_id="DEV-WATER-001",
    ratio=8.0,
    ubci_score=72.0,
):
    return {
        "case_id": case_id,
        "tenant_id": "DEV_TEST",
        "policy_version": (
            "UBCI_SPEC_V2.0.0.0"
        ),
        "is_mint": False,
        "defects": [
            {
                "type": "WATER_DAMAGE",
                "location": "INNER_PAGE",
                "ratio": ratio,
                "confidence": 0.94,
            }
        ],
        "vision_confidence": 0.94,
        "ubci_score": ubci_score,
        "predicted_grade": "B",
        "score_breakdown": [
            {
                "type": "WATER_DAMAGE",
                "applied_penalty": (
                    100.0 - ubci_score
                ),
            }
        ],
        "policy_confidence": 0.93,
        "final_decision": (
            "APPROVE_DOWNGRADE"
        ),
        "primary_reason_code": (
            "DMG_EXT_WET"
        ),
        "target_grade": "B",
        "final_grade": "B",
        "source": "SEED",
        "is_authoritative": True,
        "reviewed_at": (
            "2026-07-29T15:00:00+09:00"
        ),
    }


# 공통 검색 결과
def make_retrieved_cases():
    return [
        {
            "case_id": "DEV-WATER-001",
            "distance": 0.21,
            "content": (
                "판례 ID: DEV-WATER-001\n"
                "결함: WATER_DAMAGE\n"
                "예측 등급: B\n"
                "최종 등급: B"
            ),
            "metadata": {
                "case_id": "DEV-WATER-001",
                "tenant_id": "DEV_TEST",
                "policy_version": (
                    "UBCI_SPEC_V2.0.0.0"
                ),
                "is_authoritative": True,
            },
        },
        {
            "case_id": "DEV-WATER-002",
            "distance": 0.24,
            "content": (
                "판례 ID: DEV-WATER-002\n"
                "결함: WATER_DAMAGE\n"
                "예측 등급: B\n"
                "최종 등급: B"
            ),
            "metadata": {
                "case_id": "DEV-WATER-002",
                "tenant_id": "DEV_TEST",
                "policy_version": (
                    "UBCI_SPEC_V2.0.0.0"
                ),
                "is_authoritative": True,
            },
        },
    ]


# 공통 RAG 성공 결과
def make_rag_result():
    return {
        "reason_code": "OK",
        "repair_directive": None,
        "critic_rag_used": True,
        "critic_retrieved_case_ids": [
            "DEV-WATER-001",
            "DEV-WATER-002",
        ],
        "critic_retrieval_scores": [
            0.21,
            0.24,
        ],
        "critic_retrieval_count": 2,
        "critic_decision_source": (
            "RULE_AND_RAG"
        ),
        "critic_explanation": (
            "현재 결과와 확정 판례가 "
            "일관적입니다."
        ),
        "critic_rag_confidence": 0.95,
        "critic_prompt_version": (
            "CRITIC_CASE_RAG_V1"
        ),
    }


# LLM 대역 설치
def install_fake_llm(
    monkeypatch,
    decision=None,
    error=None,
):
    class FakeStructuredLLM:
        def invoke(self, messages):
            if error is not None:
                raise error
            return decision

    class FakeChatOpenAI:
        def __init__(self, *args, **kwargs):
            pass

        def with_structured_output(
            self,
            schema,
        ):
            return FakeStructuredLLM()

    monkeypatch.setattr(
        critic_cases,
        "ChatOpenAI",
        FakeChatOpenAI,
    )


# JSON 안전 변환
def json_safe(value):
    if value is None:
        return None

    if isinstance(
        value,
        (str, int, float, bool),
    ):
        return value

    if isinstance(value, dict):
        return {
            str(key): json_safe(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple)):
        return [
            json_safe(item)
            for item in value
        ]

    if hasattr(value, "content"):
        return {
            "message_type": (
                type(value).__name__
            ),
            "content": json_safe(
                value.content
            ),
        }

    return str(value)


# 판례 스키마 정상 검증
def test_case_schema_accepts_valid_case():
    case = CriticCase.model_validate(
        make_case()
    )

    assert case.case_id == "DEV-WATER-001"
    assert case.ubci_score == 72.0
    assert isinstance(
        case.ubci_score,
        float,
    )
    assert case.target_grade == "B"
    assert case.final_grade == "B"


# 하향 승인 등급 누락 차단
def test_case_schema_rejects_invalid_downgrade():
    invalid_case = make_case()
    invalid_case["target_grade"] = None

    with pytest.raises(ValidationError):
        CriticCase.model_validate(
            invalid_case
        )

# 권위 판례의 관리자 확정 정보 검증
@pytest.mark.parametrize(
    "missing_field",
    [
        "primary_reason_code",
        "reviewed_at",
    ],
)
def test_authoritative_case_requires_review_data(
    missing_field,
):
    invalid_case = make_case()
    invalid_case[missing_field] = None

    with pytest.raises(
        ValidationError,
        match="권위 판례",
    ):
        CriticCase.model_validate(
            invalid_case
        )


# 운영 컬렉션의 가상 권위 판례 차단
@pytest.mark.parametrize(
    "collection_name",
    [
        "wms_critic_cases",
        "wms_critic_cases_prod",
    ],
)
def test_production_collection_rejects_seed_case(
    monkeypatch,
    collection_name,
):
    monkeypatch.setattr(
        critic_cases,
        "CRITIC_CASE_COLLECTION_NAME",
        collection_name,
    )

    class ForbiddenStore:
        def add_documents(self, *args, **kwargs):
            pytest.fail(
                "운영 컬렉션 저장이 "
                "실행되면 안 됩니다."
            )

    monkeypatch.setattr(
        critic_cases,
        "get_case_vectorstore",
        lambda: ForbiddenStore(),
    )

    with pytest.raises(
        ValueError,
        match="SEED 판례",
    ):
        critic_cases.upsert_critic_case(
            make_case()
        )


# 검색 질의 상태 포함 검증
def test_search_query_contains_state_values():
    query = critic_cases.build_search_query(
        make_state()
    )

    assert "WATER_DAMAGE" in query
    assert "72.5" in query
    assert "DEV_TEST" in query
    assert "UBCI_SPEC_V2.0.0.0" in query


# 동적 Few-shot 프롬프트 검증
def test_dynamic_few_shot_contains_state_and_cases():
    messages = (
        critic_cases.build_dynamic_few_shot(
            make_state(),
            make_retrieved_cases(),
        )
    )

    assert len(messages) == 2
    assert "WATER_DAMAGE" in messages[1].content
    assert (
        "DEV-WATER-001"
        in messages[1].content
    )
    assert (
        "DEV-WATER-002"
        in messages[1].content
    )
    assert (
        "supporting_case_ids"
        in messages[0].content
    )


# 검색 결과 없음 처리
def test_empty_search_returns_rule_only(
    monkeypatch,
):
    monkeypatch.setattr(
        critic_cases,
        "search_similar_cases",
        lambda state: [],
    )

    result = (
        critic_cases
        .evaluate_with_precedents(
            make_state()
        )
    )

    assert result["reason_code"] == "OK"
    assert result["critic_rag_used"] is False
    assert (
        result["critic_decision_source"]
        == "RULE_ONLY"
    )
    assert (
        result["critic_retrieval_count"]
        == 0
    )


# Chroma 검색 장애 처리
def test_search_error_returns_rule_fallback(
    monkeypatch,
):
    def raise_search_error(state):
        raise ConnectionError(
            "Chroma unavailable"
        )

    monkeypatch.setattr(
        critic_cases,
        "search_similar_cases",
        raise_search_error,
    )

    result = (
        critic_cases
        .evaluate_with_precedents(
            make_state()
        )
    )

    assert result["reason_code"] == "OK"
    assert result["critic_rag_used"] is False
    assert (
        result["critic_decision_source"]
        == "RULE_FALLBACK"
    )


# LLM 장애 처리
def test_llm_error_returns_rule_fallback(
    monkeypatch,
):
    monkeypatch.setattr(
        critic_cases,
        "search_similar_cases",
        lambda state: (
            make_retrieved_cases()
        ),
    )

    install_fake_llm(
        monkeypatch,
        error=RuntimeError(
            "LLM unavailable"
        ),
    )

    result = (
        critic_cases
        .evaluate_with_precedents(
            make_state()
        )
    )

    assert result["reason_code"] == "OK"
    assert result["critic_rag_used"] is False
    assert (
        result["critic_decision_source"]
        == "RULE_FALLBACK"
    )
    assert (
        result["critic_retrieval_count"]
        == 2
    )


# 판례 일치 처리
def test_consistent_cases_return_rule_and_rag(
    monkeypatch,
):
    monkeypatch.setattr(
        critic_cases,
        "search_similar_cases",
        lambda state: (
            make_retrieved_cases()
        ),
    )

    decision = CriticFewShotDecision(
        is_consistent=True,
        has_sufficient_evidence=True,
        explanation=(
            "현재 정책 결과와 판례가 "
            "일관적입니다."
        ),
        supporting_case_ids=[
            "DEV-WATER-001",
            "DEV-WATER-002",
        ],
        confidence=0.95,
        repair_directive=None,
    )

    install_fake_llm(
        monkeypatch,
        decision=decision,
    )

    result = (
        critic_cases
        .evaluate_with_precedents(
            make_state()
        )
    )

    assert result["reason_code"] == "OK"
    assert result["critic_rag_used"] is True
    assert (
        result["critic_decision_source"]
        == "RULE_AND_RAG"
    )
    assert (
        result["critic_rag_confidence"]
        == 0.95
    )


# 판례 불일치 처리
def test_inconsistent_cases_return_violation(
    monkeypatch,
):
    monkeypatch.setattr(
        critic_cases,
        "search_similar_cases",
        lambda state: (
            make_retrieved_cases()
        ),
    )

    decision = CriticFewShotDecision(
        is_consistent=False,
        has_sufficient_evidence=True,
        explanation=(
            "현재 감점과 판례의 감점이 "
            "일치하지 않습니다."
        ),
        supporting_case_ids=[
            "DEV-WATER-001",
        ],
        confidence=0.92,
        repair_directive=(
            "Policy 감점 규칙 재검토 필요"
        ),
    )

    install_fake_llm(
        monkeypatch,
        decision=decision,
    )

    result = (
        critic_cases
        .evaluate_with_precedents(
            make_state()
        )
    )

    assert (
        result["reason_code"]
        == "UBCI_POLICY_VIOLATION"
    )
    assert result["critic_rag_used"] is True
    assert (
        result["critic_decision_source"]
        == "RULE_AND_RAG"
    )
    assert result["repair_directive"]


# 가짜 판례 ID 방어
def test_unknown_supporting_case_id_falls_back(
    monkeypatch,
):
    monkeypatch.setattr(
        critic_cases,
        "search_similar_cases",
        lambda state: (
            make_retrieved_cases()
        ),
    )

    decision = CriticFewShotDecision(
        is_consistent=True,
        has_sufficient_evidence=True,
        explanation="일관적입니다.",
        supporting_case_ids=[
            "FAKE-CASE-999",
        ],
        confidence=0.99,
        repair_directive=None,
    )

    install_fake_llm(
        monkeypatch,
        decision=decision,
    )

    result = (
        critic_cases
        .evaluate_with_precedents(
            make_state()
        )
    )

    assert result["critic_rag_used"] is False
    assert (
        result["critic_decision_source"]
        == "RULE_FALLBACK"
    )


# 낮은 RAG 신뢰도 방어
def test_low_rag_confidence_keeps_rule_only(
    monkeypatch,
):
    monkeypatch.setattr(
        critic_cases,
        "search_similar_cases",
        lambda state: (
            make_retrieved_cases()
        ),
    )

    decision = CriticFewShotDecision(
        is_consistent=True,
        has_sufficient_evidence=True,
        explanation=(
            "유사하지만 근거가 약합니다."
        ),
        supporting_case_ids=[
            "DEV-WATER-001",
        ],
        confidence=0.50,
        repair_directive=None,
    )

    install_fake_llm(
        monkeypatch,
        decision=decision,
    )

    result = (
        critic_cases
        .evaluate_with_precedents(
            make_state()
        )
    )

    assert result["reason_code"] == "OK"
    assert result["critic_rag_used"] is False
    assert (
        result["critic_decision_source"]
        == "RULE_ONLY"
    )
    assert (
        result["critic_rag_confidence"]
        == 0.50
    )


# 정책·테넌트·권위 판례 필터
def test_search_filters_policy_tenant_and_authority(
    monkeypatch,
):
    documents = [
        (
            Document(
                page_content="정상 판례",
                metadata={
                    "case_id": "MATCH-001",
                    "tenant_id": "DEV_TEST",
                    "policy_version": (
                        "UBCI_SPEC_V2.0.0.0"
                    ),
                    "is_authoritative": True,
                },
            ),
            0.10,
        ),
        (
            Document(
                page_content="비권위 판례",
                metadata={
                    "case_id": "NOT-AUTH-001",
                    "tenant_id": "DEV_TEST",
                    "policy_version": (
                        "UBCI_SPEC_V2.0.0.0"
                    ),
                    "is_authoritative": False,
                },
            ),
            0.11,
        ),
        (
            Document(
                page_content="다른 정책 판례",
                metadata={
                    "case_id": "OLD-POLICY-001",
                    "tenant_id": "DEV_TEST",
                    "policy_version": (
                        "UBCI_SPEC_V1.0.0.0"
                    ),
                    "is_authoritative": True,
                },
            ),
            0.12,
        ),
        (
            Document(
                page_content="다른 테넌트 판례",
                metadata={
                    "case_id": "OTHER-TENANT-001",
                    "tenant_id": "OTHER",
                    "policy_version": (
                        "UBCI_SPEC_V2.0.0.0"
                    ),
                    "is_authoritative": True,
                },
            ),
            0.13,
        ),
        (
            Document(
                page_content="공통 판례",
                metadata={
                    "case_id": "GLOBAL-001",
                    "tenant_id": "GLOBAL",
                    "policy_version": (
                        "UBCI_SPEC_V2.0.0.0"
                    ),
                    "is_authoritative": True,
                },
            ),
            0.14,
        ),
    ]

    class FakeCollection:
        def count(self):
            return len(documents)

    class FakeClient:
        def get_or_create_collection(
            self,
            name,
        ):
            return FakeCollection()

    class FakeVectorStore:
        received_filter = None

        def similarity_search_with_score(
            self,
            query,
            k,
            filter,
        ):
            self.received_filter = filter
            return documents

    fake_store = FakeVectorStore()

    monkeypatch.setattr(
        critic_cases,
        "get_chroma_client",
        lambda: FakeClient(),
    )
    monkeypatch.setattr(
        critic_cases,
        "get_case_vectorstore",
        lambda: fake_store,
    )

    result = (
        critic_cases.search_similar_cases(
            make_state(),
            top_k=10,
        )
    )

    result_ids = [
        item["case_id"]
        for item in result
    ]

    assert fake_store.received_filter == {
        "$and": [
            {
                "is_authoritative": {
                    "$eq": True,
                }
            },
            {
                "policy_version": {
                    "$eq": "UBCI_SPEC_V2.0.0.0",
                }
            },
            {
                "tenant_id": {
                    "$in": [
                        "DEV_TEST",
                        "GLOBAL",
                    ],
                }
            },
        ]
    }
    assert result_ids == [
        "MATCH-001",
        "GLOBAL-001",
    ]


# Critic 규칙 우선 처리
def test_critic_hard_rule_skips_rag(
    monkeypatch,
):
    def fail_if_called(state):
        pytest.fail(
            "규칙 오류가 있으면 RAG를 "
            "호출하면 안 됩니다."
        )

    monkeypatch.setattr(
        agents,
        "evaluate_with_precedents",
        fail_if_called,
    )

    state = make_state(
        is_mint=True,
    )

    result = agents.critic_agent(state)

    assert result["reason_code"] in {
        "BBOX_MISMATCH",
        "VISION_RESULT_CONFLICT",
    }
    assert result["critic_rag_used"] is False
    assert result["revision_count"] == 1


# Critic RAG 결과 반영
def test_critic_agent_uses_rag_result(
    monkeypatch,
):
    monkeypatch.setattr(
        agents,
        "evaluate_with_precedents",
        lambda state: make_rag_result(),
    )

    result = agents.critic_agent(
        make_state()
    )

    assert result["reason_code"] == "OK"
    assert result["critic_rag_used"] is True
    assert (
        result["critic_decision_source"]
        == "RULE_AND_RAG"
    )
    assert result["revision_count"] == 0
    assert result["overall_confidence"] == 0.91


# Supervisor 라우팅 검증
@pytest.mark.parametrize(
    ("reason_code", "expected_node"),
    [
        ("OK", "report_agent"),
        (
            "UBCI_POLICY_VIOLATION",
            "policy_agent",
        ),
    ],
)
def test_supervisor_routes_critic_result(
    reason_code,
    expected_node,
):
    state = make_state(
        reason_code=reason_code,
    )

    actual_node = (
        supervisor.route_from_supervisor(
            state
        )
    )

    assert actual_node == expected_node


# 전체 LangGraph 모의 통합
def test_langgraph_critic_to_report(
    monkeypatch,
):
    monkeypatch.setattr(
        agents,
        "evaluate_with_precedents",
        lambda state: make_rag_result(),
    )

    config = {
        "configurable": {
            "thread_id": (
                f"critic-unit-{uuid.uuid4()}"
            )
        }
    }

    final_state = (
        supervisor.app_graph.invoke(
            make_state(),
            config=config,
        )
    )

    assert final_state["reason_code"] == "OK"
    assert (
        final_state["critic_rag_used"]
        is True
    )
    assert (
        final_state[
            "critic_decision_source"
        ]
        == "RULE_AND_RAG"
    )
    assert final_state["final_report"]


# 실제 Chroma·GPT·LangGraph 통합
@pytest.mark.skipif(
    not RUN_LIVE,
    reason=(
        "RUN_CRITIC_RAG_LIVE=1 설정 시 "
        "실행되는 실제 통합 테스트"
    ),
)
def test_live_chroma_gpt_langgraph():
    assert (
        critic_cases
        .CRITIC_CASE_COLLECTION_NAME
        == "wms_critic_cases_dev_test"
    ), (
        "실제 테스트는 운영 컬렉션이 아닌 "
        "wms_critic_cases_dev_test에서만 "
        "실행해야 합니다."
    )

    critic_cases.get_chroma_client.cache_clear()
    critic_cases.get_case_vectorstore.cache_clear()

    live_cases = [
        make_case(
            case_id="DEV-WATER-001",
            ratio=7.5,
            ubci_score=73.0,
        ),
        make_case(
            case_id="DEV-WATER-002",
            ratio=8.0,
            ubci_score=72.0,
        ),
        make_case(
            case_id="DEV-WATER-003",
            ratio=9.0,
            ubci_score=70.0,
        ),
    ]

    stored_ids = [
        critic_cases.upsert_critic_case(
            case
        )
        for case in live_cases
    ]

    assert stored_ids == [
        "DEV-WATER-001",
        "DEV-WATER-002",
        "DEV-WATER-003",
    ]

    config = {
        "configurable": {
            "thread_id": (
                f"critic-live-{uuid.uuid4()}"
            )
        }
    }

    events = list(
        supervisor.app_graph.stream(
            make_state(),
            config=config,
            stream_mode="updates",
        )
    )

    snapshot = (
        supervisor.app_graph.get_state(
            config
        )
    )
    final_state = snapshot.values

    node_sequence = [
        next(iter(event.keys()))
        for event in events
        if event
    ]

    evidence = {
        "collection": (
            critic_cases
            .CRITIC_CASE_COLLECTION_NAME
        ),
        "stored_case_ids": stored_ids,
        "node_sequence": node_sequence,
        "reason_code": final_state.get(
            "reason_code"
        ),
        "critic_rag_used": final_state.get(
            "critic_rag_used"
        ),
        "critic_retrieved_case_ids": (
            final_state.get(
                "critic_retrieved_case_ids"
            )
        ),
        "critic_retrieval_scores": (
            final_state.get(
                "critic_retrieval_scores"
            )
        ),
        "critic_decision_source": (
            final_state.get(
                "critic_decision_source"
            )
        ),
        "critic_explanation": (
            final_state.get(
                "critic_explanation"
            )
        ),
        "critic_rag_confidence": (
            final_state.get(
                "critic_rag_confidence"
            )
        ),
        "critic_prompt_version": (
            final_state.get(
                "critic_prompt_version"
            )
        ),
        "final_report": final_state.get(
            "final_report"
        ),
        "events": json_safe(events),
    }

    print(
        "\n=== LIVE LANGGRAPH EVIDENCE ==="
    )
    print(
        json.dumps(
            evidence,
            ensure_ascii=False,
            indent=2,
        )
    )

    assert "critic_agent" in node_sequence
    assert "report_agent" in node_sequence
    assert final_state["reason_code"] == "OK"
    assert (
        final_state["critic_rag_used"]
        is True
    )
    assert (
        final_state[
            "critic_decision_source"
        ]
        == "RULE_AND_RAG"
    )
    assert (
        final_state[
            "critic_retrieval_count"
        ]
        >= 1
    )
    assert final_state["final_report"]

# 잘못된 revision_count 안전 처리
def test_invalid_revision_count_returns_quality_error(
    monkeypatch,
):
    def fail_if_called(state):
        pytest.fail(
            "규칙 오류가 있으면 RAG를 "
            "호출하면 안 됩니다."
        )

    monkeypatch.setattr(
        agents,
        "evaluate_with_precedents",
        fail_if_called,
    )

    result = agents.critic_agent(
        make_state(
            revision_count="bad",
        )
    )

    assert result["reason_code"] == "QUALITY_ERROR"
    assert result["revision_count"] == 1
    assert result["critic_rag_used"] is False