from decimal import Decimal
from unittest.mock import Mock
from uuid import uuid4

from app.models.wms import BookCategory, ConditionGrade
from app.domains.pricing.schemas.pricing import PricingRecommendationResponse
from app.domains.pricing.dynamic_pricing_service import execute_dynamic_pricing
from app.domains.pricing.pricing_context_service import (
    DynamicPricingContext,
    DynamicPricingResult,
)


def test_execute_dynamic_pricing_builds_agent_request_and_saves_result(
    monkeypatch,
):
    inventory_used_item_id = uuid4()
    book_id = uuid4()
    context = DynamicPricingContext(
        inventory_used_item_id=inventory_used_item_id,
        lpn_barcode="LPN-TEST-001",
        book_id=book_id,
        isbn="9788912345678",
        base_price=Decimal("18000.00"),
        category=BookCategory.NOVEL,
        ubci_score=Decimal("91.50"),
        condition_grade=ConditionGrade.EXCELLENT,
    )
    saved_result = DynamicPricingResult(
        inventory_used_item_id=inventory_used_item_id,
        lpn_barcode="LPN-TEST-001",
        base_price=Decimal("18000.00"),
        discount_rate=Decimal("0.1500"),
        sale_price=Decimal("15300.00"),
        pricing_changed=True,
    )
    get_context = Mock(return_value=context)
    save_result = Mock(return_value=saved_result)
    agent = Mock(
        return_value=PricingRecommendationResponse(
            final_price=15300,
            discount_rate=15,
            pricing_reason=("도서 상태와 카테고리 수요를 반영한 가격입니다."),
        )
    )
    monkeypatch.setattr(
        "app.domains.pricing.dynamic_pricing_service.get_dynamic_pricing_context",
        get_context,
    )
    monkeypatch.setattr(
        "app.domains.pricing.dynamic_pricing_service.apply_dynamic_pricing_result",
        save_result,
    )
    session = Mock()

    result = execute_dynamic_pricing(
        session,
        "LPN-TEST-001",
        agent=agent,
    )

    request = agent.call_args.args[0]
    assert request.base_price == Decimal("18000.00")
    assert request.category == BookCategory.NOVEL
    assert request.ubci_score == 91.5
    assert request.condition_grade == ConditionGrade.EXCELLENT
    save_result.assert_called_once_with(
        session,
        lpn_barcode="LPN-TEST-001",
        discount_rate=Decimal("0.1500"),
        final_price=Decimal("15300.00"),
    )
    assert result is saved_result


def test_execute_dynamic_pricing_does_not_manage_transaction(monkeypatch):
    context = DynamicPricingContext(
        inventory_used_item_id=uuid4(),
        lpn_barcode="LPN-TEST-002",
        book_id=uuid4(),
        isbn=None,
        base_price=Decimal("20000.00"),
        category=BookCategory.HUMANITIES,
        ubci_score=Decimal("96.00"),
        condition_grade=ConditionGrade.MINT,
    )
    monkeypatch.setattr(
        "app.domains.pricing.dynamic_pricing_service.get_dynamic_pricing_context",
        Mock(return_value=context),
    )
    monkeypatch.setattr(
        "app.domains.pricing.dynamic_pricing_service.apply_dynamic_pricing_result",
        Mock(
            return_value=DynamicPricingResult(
                inventory_used_item_id=context.inventory_used_item_id,
                lpn_barcode=context.lpn_barcode,
                base_price=context.base_price,
                discount_rate=Decimal("0.1000"),
                sale_price=Decimal("18000.00"),
                pricing_changed=True,
            )
        ),
    )
    session = Mock()

    execute_dynamic_pricing(
        session,
        context.lpn_barcode,
        agent=Mock(
            return_value=PricingRecommendationResponse(
                final_price=18000,
                discount_rate=10,
                pricing_reason=("도서 상태와 카테고리 수요를 반영한 가격입니다."),
            )
        ),
    )

    session.commit.assert_not_called()
    session.rollback.assert_not_called()
