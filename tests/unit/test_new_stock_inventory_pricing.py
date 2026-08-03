from decimal import Decimal

import pytest

from app.domain.inventory_pricing_policy import (
    calculate_new_stock_default_price,
)
from app.models.wms import Inventory


def test_new_stock_default_price_applies_ten_percent_discount():
    discount_rate, sale_price = calculate_new_stock_default_price(
        Decimal("18000.00")
    )

    assert discount_rate == Decimal("0.1000")
    assert sale_price == Decimal("16200.00")


def test_new_stock_default_price_rejects_non_positive_base_price():
    with pytest.raises(ValueError):
        calculate_new_stock_default_price(Decimal("0"))


def test_new_stock_inventory_has_pricing_columns_and_constraints():
    columns = Inventory.__table__.columns

    assert columns["discount_rate"].type.precision == 5
    assert columns["discount_rate"].type.scale == 4
    assert columns["sale_price"].type.precision == 12
    assert columns["sale_price"].type.scale == 2

    check_names = {
        constraint.name
        for constraint in Inventory.__table__.constraints
        if constraint.name is not None
    }
    assert "ck_inventory_discount_rate" in check_names
    assert "ck_inventory_sale_price_positive" in check_names
    assert "ck_inventory_pricing_pair" in check_names
