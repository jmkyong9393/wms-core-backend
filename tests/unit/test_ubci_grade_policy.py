from decimal import Decimal

import pytest

from app.domain.ubci_grade_policy import determine_condition_grade
from app.models.wms import ConditionGrade


@pytest.mark.parametrize(
    ("score", "expected_grade"),
    [
        (100, ConditionGrade.MINT),
        (95, ConditionGrade.MINT),
        (Decimal("95.00"), ConditionGrade.MINT),
        (94.99, ConditionGrade.EXCELLENT),
        (94, ConditionGrade.EXCELLENT),
        (85, ConditionGrade.EXCELLENT),
        (84.99, ConditionGrade.NORMAL),
        (84, ConditionGrade.NORMAL),
        (65, ConditionGrade.NORMAL),
        (64.99, ConditionGrade.REJECT),
        (64, ConditionGrade.REJECT),
        (0, ConditionGrade.REJECT),
    ],
)
def test_determine_condition_grade_by_score(score, expected_grade):
    assert determine_condition_grade(score) == expected_grade


@pytest.mark.parametrize(
    "defect_code",
    [
        "WATER_DAMAGE",
        "PAGE_WARPING",
        "BINDING_SEPARATION",
        "MISSING_PAGE",
        "EXCESSIVE_MARKING",
    ],
)
def test_fatal_defect_rejects_regardless_of_score(defect_code):
    defects = [{"type": defect_code}]

    assert determine_condition_grade(100, defects) == ConditionGrade.REJECT


def test_more_than_ten_marked_pages_is_rejected():
    defects = [{"type": "MARKED_PAGES", "page_count": 11}]

    assert determine_condition_grade(100, defects) == ConditionGrade.REJECT


def test_ten_marked_pages_uses_score_grade():
    defects = [{"type": "MARKED_PAGES", "page_count": 10}]

    assert determine_condition_grade(95, defects) == ConditionGrade.MINT


@pytest.mark.parametrize("score", [-1, 101])
def test_score_outside_policy_range_is_rejected(score):
    with pytest.raises(ValueError):
        determine_condition_grade(score)


@pytest.mark.parametrize("score", [True, "95", None])
def test_non_numeric_score_is_rejected(score):
    with pytest.raises(TypeError):
        determine_condition_grade(score)


@pytest.mark.parametrize("score", [float("inf"), float("-inf"), float("nan")])
def test_non_finite_score_is_rejected(score):
    with pytest.raises(ValueError):
        determine_condition_grade(score)
