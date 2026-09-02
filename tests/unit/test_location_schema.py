from sqlalchemy import CheckConstraint

from app.models.wms import Location


def _check_constraint_names() -> set[str]:
    return {constraint.name for constraint in Location.__table__.constraints if isinstance(constraint, CheckConstraint)}


def test_location_has_warehouse_policy_constraints():
    assert _check_constraint_names() == {
        "ck_locations_zone",
        "ck_locations_rack_positive_integer",
        "ck_locations_shelf_range",
        "ck_locations_barcode_components",
    }


def test_location_barcode_is_required_and_unique():
    barcode_column = Location.__table__.columns["barcode"]

    assert barcode_column.nullable is False
    assert barcode_column.unique is True


def test_location_uses_canonical_barcode_components():
    location = Location(
        zone="B",
        rack="3",
        shelf="10",
        barcode="B-3-10",
    )

    assert location.barcode == (f"{location.zone}-{location.rack}-{location.shelf}")
