"""Report Agent 고객 서술 생성 검증 (LLM 비활성 폴백 경로)."""

import pytest

from app.ai.agents.report import build_customer_narrative


@pytest.fixture(autouse=True)
def disable_llm(monkeypatch):
    monkeypatch.setenv("REPORT_NARRATIVE_LLM_ENABLED", "false")


def test_graded_book_narrative_lists_defects():
    narrative = build_customer_narrative(
        "B",
        [{"type": "COVER_TEAR", "location": "FRONT_COVER"}],
        72,
    )
    assert narrative["narrative_source"] == "FALLBACK_RULE"
    assert "B등급" in narrative["customer_message"]
    assert narrative["condition_notes"] == ["표지 찢어짐 (앞표지)"]


def test_reject_narrative_is_firm_without_blame():
    narrative = build_customer_narrative("REJECT", [], None)
    assert "매입이 어려운" in narrative["customer_message"]
    # 고객 노출 경계: 귀책 단어를 쓰지 않는다
    assert "귀책" not in narrative["customer_message"]
    assert "잘못" not in narrative["customer_message"]


def test_mint_narrative_has_no_notes():
    narrative = build_customer_narrative("S", [], 100)
    assert narrative["condition_notes"] == []
    assert "S등급" in narrative["customer_message"]


def test_narrative_never_leaks_internal_codes():
    """고객 노출 경계: 결함·위치 영문 enum이 그대로 나가면 안 된다."""
    narrative = build_customer_narrative(
        "A",
        [
            {"type": "COVER_TEAR", "location": "FRONT_COVER"},
            {"type": "EDGE_WEAR", "location": "BOOK_EDGE"},
        ],
        88,
    )
    rendered = narrative["customer_message"] + " ".join(
        narrative["condition_notes"]
    )
    for internal_code in (
        "COVER_TEAR",
        "EDGE_WEAR",
        "FRONT_COVER",
        "BOOK_EDGE",
    ):
        assert internal_code not in rendered
