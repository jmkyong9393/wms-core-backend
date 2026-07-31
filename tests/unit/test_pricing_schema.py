from app.schemas.pricing import DynamicPricingContextResponse


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
