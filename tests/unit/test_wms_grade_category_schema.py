from uuid import uuid4

from app.models.wms import (
    Book,
    BookCategory,
    ConditionGrade,
    InboundItem,
)


def test_condition_grade_includes_new_without_changing_used_grades():
    assert [grade.value for grade in ConditionGrade] == [
        "NEW",
        "MINT",
        "EXCELLENT",
        "NORMAL",
        "REJECT",
    ]


def test_book_category_uses_fixed_internal_values():
    assert [category.value for category in BookCategory] == [
        "COMIC",
        "STUDY_GUIDE",
        "NOVEL",
        "HUMANITIES",
        "SOCIAL_SCIENCE",
        "BUSINESS_ECONOMICS",
        "SCIENCE_TECHNOLOGY",
        "CHILDREN",
        "LANGUAGE",
        "ART_LIFESTYLE",
    ]


def test_new_book_and_inbound_item_store_category_and_grade():
    book = Book(
        title="신간 소설",
        category=BookCategory.NOVEL,
    )
    inbound_item = InboundItem(
        inbound_job_id=uuid4(),
        book_id=book.id,
        quantity=1,
        condition_grade=ConditionGrade.NEW,
    )

    assert book.category == BookCategory.NOVEL
    assert inbound_item.condition_grade == ConditionGrade.NEW
