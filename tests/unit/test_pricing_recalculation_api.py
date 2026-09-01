from decimal import Decimal
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.domains.pricing import pricing
from app.domains.pricing.pricing_context_service import (
    DynamicPricingResult,
    PricingContextIncompleteError,
    PricingContextNotFoundError,
)


def _pricing_result() -> DynamicPricingResult:
    return DynamicPricingResult(
        inventory_used_item_id=uuid4(),
        lpn_barcode="LPN-RECALCULATE-001",
        base_price=Decimal("18000.00"),
        discount_rate=Decimal("0.1500"),
        sale_price=Decimal("15300.00"),
        pricing_changed=True,
    )


def test_recalculates_and_commits_lpn_price(monkeypatch):
    result = _pricing_result()
    execute_pricing = Mock(return_value=result)
    monkeypatch.setattr(
        pricing,
        "execute_dynamic_pricing",
        execute_pricing,
    )
    session = Mock()

    response = pricing.recalculate_dynamic_pricing(
        lpn_barcode=result.lpn_barcode,
        session=session,
    )

    execute_pricing.assert_called_once_with(
        session=session,
        lpn_barcode=result.lpn_barcode,
    )
    session.commit.assert_called_once()
    session.rollback.assert_not_called()
    assert response.sale_price == Decimal("15300.00")
    assert response.discount_rate == Decimal("0.1500")


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (PricingContextNotFoundError(), 404),
        (PricingContextIncompleteError("LPN is not AVAILABLE"), 409),
    ],
)
def test_recalculation_maps_context_errors(
    monkeypatch,
    error,
    expected_status,
):
    monkeypatch.setattr(
        pricing,
        "execute_dynamic_pricing",
        Mock(side_effect=error),
    )
    session = Mock()

    with pytest.raises(HTTPException) as exc_info:
        pricing.recalculate_dynamic_pricing(
            lpn_barcode="LPN-RECALCULATE-UNKNOWN",
            session=session,
        )

    assert exc_info.value.status_code == expected_status
    session.rollback.assert_called_once()
    session.commit.assert_not_called()
