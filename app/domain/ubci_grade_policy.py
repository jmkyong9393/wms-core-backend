from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.models.wms import ConditionGrade


@dataclass(frozen=True)
class GradeThreshold:
    minimum_score: Decimal
    grade: ConditionGrade


GRADE_THRESHOLDS = (
    GradeThreshold(minimum_score=Decimal("95"), grade=ConditionGrade.MINT),
    GradeThreshold(minimum_score=Decimal("85"), grade=ConditionGrade.EXCELLENT),
    GradeThreshold(minimum_score=Decimal("65"), grade=ConditionGrade.NORMAL),
)

FATAL_DEFECT_CODES = frozenset(
    {
        "WATER_DAMAGE",
        "PAGE_WARPING",
        "BINDING_SEPARATION",
        "MISSING_PAGE",
        "EXCESSIVE_MARKING",
    }
)
MARKED_PAGES_DEFECT_CODE = "MARKED_PAGES"
MAX_ACCEPTABLE_MARKED_PAGES = 10


def determine_condition_grade(
    ubci_score: int | float | Decimal,
    defects: Iterable[Mapping[str, Any]] | None = None,
) -> ConditionGrade:
    """Return the inventory grade after applying fatal-defect overrides."""
    normalized_score = _normalize_score(ubci_score)

    if _contains_fatal_defect(defects or ()):
        return ConditionGrade.REJECT

    for threshold in GRADE_THRESHOLDS:
        if normalized_score >= threshold.minimum_score:
            return threshold.grade

    return ConditionGrade.REJECT


def _normalize_score(ubci_score: int | float | Decimal) -> Decimal:
    if isinstance(ubci_score, bool) or not isinstance(
        ubci_score,
        (int, float, Decimal),
    ):
        raise TypeError("ubci_score must be numeric")

    normalized_score = Decimal(str(ubci_score))
    if not normalized_score.is_finite():
        raise ValueError("ubci_score must be finite")
    if not Decimal("0") <= normalized_score <= Decimal("100"):
        raise ValueError("ubci_score must be between 0 and 100")

    return normalized_score


def _contains_fatal_defect(defects: Iterable[Mapping[str, Any]]) -> bool:
    for defect in defects:
        defect_code = str(defect.get("type", "")).strip().upper()

        if defect_code in FATAL_DEFECT_CODES:
            return True

        if defect_code == MARKED_PAGES_DEFECT_CODE:
            page_count = defect.get("page_count")
            if (
                isinstance(page_count, int)
                and not isinstance(page_count, bool)
                and page_count > MAX_ACCEPTABLE_MARKED_PAGES
            ):
                return True

    return False
