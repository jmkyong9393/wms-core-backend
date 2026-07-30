import pytest

from app.domain.warehouse_location_policy import (
    CATEGORY_RACK_MAP,
    USED_GRADE_ZONE_MAP,
    SHELF_CAPACITY,
    SHELF_COUNT_PER_RACK,
    build_location_barcode,
    rack_for_category,
    zone_for_used_grade,
)
from app.models.wms import BookCategory, ConditionGrade


@pytest.mark.parametrize(
    ("category", "expected_rack"),
    [
        (BookCategory.COMIC, "1"),
        (BookCategory.STUDY_GUIDE, "2"),
        (BookCategory.NOVEL, "3"),
        (BookCategory.HUMANITIES, "4"),
        (BookCategory.SOCIAL_SCIENCE, "5"),
        (BookCategory.BUSINESS_ECONOMICS, "6"),
        (BookCategory.SCIENCE_TECHNOLOGY, "7"),
        (BookCategory.CHILDREN, "8"),
        (BookCategory.LANGUAGE, "9"),
        (BookCategory.ART_LIFESTYLE, "10"),
    ],
)
def test_category_maps_to_fixed_rack(category, expected_rack):
    assert rack_for_category(category) == expected_rack


@pytest.mark.parametrize(
    ("grade", "expected_zone"),
    [
        (ConditionGrade.MINT, "B"),
        (ConditionGrade.EXCELLENT, "B"),
        (ConditionGrade.NORMAL, "B"),
        (ConditionGrade.REJECT, "C"),
    ],
)
def test_used_grade_maps_to_warehouse_zone(grade, expected_zone):
    assert zone_for_used_grade(grade) == expected_zone


def test_policy_covers_every_category_and_used_grade():
    assert set(CATEGORY_RACK_MAP) == set(BookCategory)
    assert set(USED_GRADE_ZONE_MAP) == set(ConditionGrade)


def test_shelf_policy_uses_ten_shelves_with_demo_capacity():
    assert SHELF_COUNT_PER_RACK == 10
    assert SHELF_CAPACITY == 9999


def test_location_barcode_uses_zone_rack_shelf_components():
    assert build_location_barcode("B", "3", "7") == "B-3-7"


def test_policy_maps_are_read_only():
    with pytest.raises(TypeError):
        CATEGORY_RACK_MAP[BookCategory.NOVEL] = "9"
