from decimal import Decimal

import pytest

from pydantic import ValidationError

from app.ai import pricing_agent as pricing_module
from app.domains.pricing.schemas.pricing import (
    PricingReason,
    PricingRecommendationRequest,
)


def make_request(
    **overrides,
) -> PricingRecommendationRequest:
    """공통 Pricing Agent 입력값 생성."""

    data = {
        "base_price": Decimal("18000.00"),
        "category": "NOVEL",
        "ubci_score": 91.5,
        "condition_grade": "EXCELLENT",
    }

    data.update(overrides)

    return PricingRecommendationRequest(**data)


@pytest.mark.parametrize(
    ("price", "expected"),
    [
        (Decimal("32749"), 32700),
        (Decimal("32750"), 32800),
        (Decimal("32800"), 32800),
    ],
)
def test_round_to_hundred(
    price,
    expected,
):
    """100원 단위 반올림 검증."""

    assert pricing_module.round_to_hundred(price) == expected


def test_calculate_final_price():
    """카테고리와 UBCI 기반 가격 계산 검증."""

    final_price, raw_price, category_label = pricing_module.calculate_final_price(make_request())

    assert raw_price == Decimal("12038.4")
    assert final_price == 12000
    assert category_label == "소설·시·희곡"


@pytest.mark.parametrize(
    ("category", "expected_retention"),
    [
        ("COMIC", Decimal("0.78")),
        ("STUDY_GUIDE", Decimal("0.74")),
        ("NOVEL", Decimal("0.76")),
        ("HUMANITIES", Decimal("0.66")),
        ("SOCIAL_SCIENCE", Decimal("0.64")),
        ("BUSINESS_ECONOMICS", Decimal("0.69")),
        ("SCIENCE_TECHNOLOGY", Decimal("0.71")),
        ("CHILDREN", Decimal("0.68")),
        ("LANGUAGE", Decimal("0.72")),
        ("ART_LIFESTYLE", Decimal("0.65")),
    ],
)
def test_category_policy(
    category,
    expected_retention,
):
    """문서에 정의된 카테고리 보존계수 검증."""

    _, actual_retention = pricing_module.CATEGORY_POLICY[category]

    assert actual_retention == expected_retention


@pytest.mark.parametrize(
    ("ubci_score", "expected"),
    [
        (100.0, Decimal("1.00")),
        (95.0, Decimal("0.95")),
        (90.0, Decimal("0.85")),
        (85.0, Decimal("0.75")),
        (80.0, Decimal("0.675")),
        (75.0, Decimal("0.60")),
        (70.0, Decimal("0.525")),
        (65.0, Decimal("0.45")),
    ],
)
def test_condition_retention(
    ubci_score,
    expected,
):
    """UBCI 등급별 상태 보존계수 검증."""

    assert pricing_module.calculate_condition_retention(ubci_score) == expected


def test_condition_retention_is_monotonic():
    """UBCI 상승에 따른 상태 보존계수 단조 증가 검증."""

    scores = range(
        65,
        101,
    )

    retentions = [pricing_module.calculate_condition_retention(score) for score in scores]

    assert retentions == sorted(retentions)


def test_pricing_agent_returns_llm_reason(
    monkeypatch,
):
    """최종 가격과 LLM 가격 선정 사유 반환 검증."""

    class FakeStructuredLLM:
        def invoke(self, messages):
            return PricingReason(
                pricing_reason=(
                    "소설 카테고리 가격 정책과 UBCI 91.5점의 "
                    "우수한 보존 상태를 반영했습니다. "
                    "검증된 계산 결과에 따라 최종 가격을 "
                    "12,000원으로 책정했습니다."
                )
            )

    class FakeChatOpenAI:
        def __init__(
            self,
            *args,
            **kwargs,
        ):
            pass

        def with_structured_output(
            self,
            *args,
            **kwargs,
        ):
            return FakeStructuredLLM()

    monkeypatch.setenv(
        "OPENAI_API_KEY",
        "test-key",
    )

    monkeypatch.setattr(
        pricing_module,
        "ChatOpenAI",
        FakeChatOpenAI,
    )

    result = pricing_module.pricing_agent(make_request())

    assert result.final_price == 12000
    assert result.discount_rate == 34
    assert "UBCI 91.5점" in result.pricing_reason


def test_missing_api_key_uses_fallback(
    monkeypatch,
):
    """API 키 누락 시 기본 가격 선정 사유 반환 검증."""

    monkeypatch.delenv(
        "OPENAI_API_KEY",
        raising=False,
    )

    result = pricing_module.pricing_agent(make_request())

    assert result.final_price == 12000
    assert result.discount_rate == 34
    assert "소설·시·희곡" in result.pricing_reason
    assert "12,000원" in result.pricing_reason


def test_invalid_category_is_rejected():
    """허용되지 않은 카테고리 차단 검증."""

    with pytest.raises(
        ValidationError,
    ):
        make_request(
            category="UNKNOWN",
        )


@pytest.mark.parametrize(
    "ubci_score",
    [
        0,
        64.9,
        100.1,
    ],
)
def test_invalid_ubci_score_is_rejected(
    ubci_score,
):
    """가격 계산 불가능한 UBCI 점수 차단 검증."""

    with pytest.raises(
        ValidationError,
    ):
        make_request(
            ubci_score=ubci_score,
        )


@pytest.mark.parametrize(
    (
        "base_price",
        "final_price",
        "expected",
    ),
    [
        (
            Decimal("18000"),
            12000,
            34,
        ),
        (
            Decimal("20000"),
            15000,
            25,
        ),
        (
            Decimal("10000"),
            10000,
            0,
        ),
    ],
)
def test_calculate_discount_rate(
    base_price,
    final_price,
    expected,
):
    """정가 대비 정수 할인율 검증."""

    assert (
        pricing_module.calculate_discount_rate(
            base_price,
            final_price,
        )
        == expected
    )


def test_invalid_base_price_is_rejected():
    """정가 최소 범위 검증."""

    with pytest.raises(
        ValidationError,
    ):
        make_request(
            base_price=Decimal("99"),
        )
