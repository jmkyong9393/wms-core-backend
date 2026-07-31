from app.schemas.pricing import (
    DynamicPricingContextResponse,
    DynamicPricingResultRequest,
    DynamicPricingResultResponse,
)


def test_dynamic_pricing_context_schema_exposes_agent_inputs():
    schema = DynamicPricingContextResponse.model_json_schema()

    assert {
        "inventory_used_item_id",
        "lpn_barcode",
        "book_id",
        "isbn",
        "base_price",
        "category",
        "ubci_score",
        "condition_grade",
    }.issubset(schema["properties"])


def test_dynamic_pricing_result_schema_exposes_agent_output():
    request_schema = DynamicPricingResultRequest.model_json_schema()
    response_schema = DynamicPricingResultResponse.model_json_schema()

    assert {
        "lpn_barcode",
        "discount_rate",
        "final_price",
    }.issubset(request_schema["properties"])
    assert {
        "inventory_used_item_id",
        "lpn_barcode",
        "base_price",
        "discount_rate",
        "sale_price",
        "pricing_changed",
    }.issubset(response_schema["properties"])
