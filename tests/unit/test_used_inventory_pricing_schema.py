from app.models.wms import InventoryUsedItem


def test_used_inventory_has_lpn_sale_pricing_columns():
    columns = InventoryUsedItem.__table__.columns

    assert columns["discount_rate"].type.precision == 5
    assert columns["discount_rate"].type.scale == 4
    assert columns["sale_price"].type.precision == 12
    assert columns["sale_price"].type.scale == 2


def test_used_inventory_pricing_constraints_are_registered():
    check_names = {
        constraint.name
        for constraint in InventoryUsedItem.__table__.constraints
        if constraint.name is not None
    }

    assert "ck_inventory_used_items_discount_rate" in check_names
    assert "ck_inventory_used_items_sale_price_positive" in check_names
    assert "ck_inventory_used_items_pricing_pair" in check_names
