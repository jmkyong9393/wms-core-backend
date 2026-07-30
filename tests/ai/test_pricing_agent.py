from decimal import Decimal

import pytest

from pydantic import ValidationError

from app.ai import pricing_agent as pricing_module
from app.schemas.pricing import (
    PricingReason,
    PricingRecommendationRequest,
)


def make_request(
    **overrides,
) -> PricingRecommendationRequest:
    """공통 Pricing Agent 입력값 생성."""

    data = {
        "book_title": "일반물리학 10판",
        "category": "전공서적",
        "original_price": Decimal("35000"),
        "ubci_score": 85.0,
        "condition_grade": "EXCELLENT",
        "base_calculated_price": Decimal(
            "32725"
        ),
        "virtual_demand": "높음",
    }

    data.update(overrides)

    return PricingRecommendationRequest(
        **data
    )


@pytest.mark.parametrize(
    ("price", "expected"),
    [
        (Decimal("32724"), 32700),
        (Decimal("32725"), 32700),
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

    assert (
        pricing_module.round_to_hundred(
            price
        )
        == expected
    )


def test_pricing_agent_returns_fixed_price(
    monkeypatch,
):
    """확정 가격과 LLM 사유 결합 검증."""

    class FakeStructuredLLM:
        def invoke(self, messages):
            return PricingReason(
                pricing_reason=(
                    "전공서적으로 꾸준한 수요가 예상됩니다. "
                    "UBCI 85점의 우수한 보존 상태를 반영하여 "
                    "최종 가격을 책정했습니다."
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

    result = pricing_module.pricing_agent(
        make_request()
    )

    assert result.final_price == 32700
    assert result.final_price % 100 == 0
    assert "UBCI 85점" in result.pricing_reason


def test_invalid_ubci_score_is_rejected():
    """UBCI 점수 범위 검증."""

    with pytest.raises(
        ValidationError,
    ):
        make_request(
            ubci_score=101,
        )


def test_missing_api_key_is_rejected(
    monkeypatch,
):
    """OpenAI API 키 누락 검증."""

    monkeypatch.delenv(
        "OPENAI_API_KEY",
        raising=False,
    )

    with pytest.raises(
        RuntimeError,
        match="OPENAI_API_KEY",
    ):
        pricing_module.pricing_agent(
            make_request()
        )