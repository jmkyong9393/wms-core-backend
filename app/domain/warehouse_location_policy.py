from types import MappingProxyType

from app.models.wms import BookCategory, ConditionGrade


SHELF_COUNT_PER_RACK = 10
SHELF_CAPACITY = 9999

CATEGORY_RACK_MAP = MappingProxyType(
    {
        BookCategory.COMIC: "1",
        BookCategory.STUDY_GUIDE: "2",
        BookCategory.NOVEL: "3",
        BookCategory.HUMANITIES: "4",
        BookCategory.SOCIAL_SCIENCE: "5",
        BookCategory.BUSINESS_ECONOMICS: "6",
        BookCategory.SCIENCE_TECHNOLOGY: "7",
        BookCategory.CHILDREN: "8",
        BookCategory.LANGUAGE: "9",
        BookCategory.ART_LIFESTYLE: "10",
    }
)

USED_GRADE_ZONE_MAP = MappingProxyType(
    {
        ConditionGrade.MINT: "B",
        ConditionGrade.EXCELLENT: "B",
        ConditionGrade.NORMAL: "B",
        ConditionGrade.REJECT: "C",
    }
)


def rack_for_category(category: BookCategory) -> str:
    return CATEGORY_RACK_MAP[category]


def zone_for_used_grade(grade: ConditionGrade) -> str:
    return USED_GRADE_ZONE_MAP[grade]


def build_location_barcode(zone: str, rack: str, shelf: str) -> str:
    return f"{zone}-{rack}-{shelf}"
