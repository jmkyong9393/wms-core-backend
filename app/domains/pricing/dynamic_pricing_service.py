from collections.abc import Callable
from decimal import Decimal

from sqlmodel import Session

from app.ai.pricing_agent import pricing_agent
from app.domains.pricing.pricing_context_service import (
    DynamicPricingResult,
    apply_dynamic_pricing_result,
    get_dynamic_pricing_context,
)
from app.domains.pricing.schemas.pricing import (
    PricingRecommendationRequest,
    PricingRecommendationResponse,
)

PricingAgent = Callable[
    [PricingRecommendationRequest],
    PricingRecommendationResponse,
]


def execute_dynamic_pricing(
    session: Session,
    lpn_barcode: str,
    *,
    agent: PricingAgent = pricing_agent,
) -> DynamicPricingResult:
    """Build Agent input from DB context and persist its validated result."""
    context = get_dynamic_pricing_context(session, lpn_barcode)
    request = PricingRecommendationRequest(
        base_price=context.base_price,
        category=context.category,
        ubci_score=float(context.ubci_score),
        condition_grade=context.condition_grade,
    )
    recommendation = agent(request)

    discount_rate = (Decimal(recommendation.discount_rate) / Decimal("100")).quantize(Decimal("0.0001"))
    final_price = Decimal(recommendation.final_price).quantize(Decimal("0.01"))

    return apply_dynamic_pricing_result(
        session,
        lpn_barcode=lpn_barcode,
        discount_rate=discount_rate,
        final_price=final_price,
    )
