import pytest

from app.domain.aladin_category_policy import (
    ALADIN_CATEGORY_KEYWORDS,
    UnsupportedAladinCategoryError,
    map_aladin_category,
)
from app.models.wms import BookCategory


@pytest.mark.parametrize(
    ("category_name", "expected"),
    [
        ("국내도서>만화>교양만화", BookCategory.COMIC),
        ("국내도서 > 수험서/자격증 > 공무원", BookCategory.STUDY_GUIDE),
        ("국내도서>소설/시/희곡>한국소설", BookCategory.NOVEL),
        ("국내도서>인문학>철학 일반", BookCategory.HUMANITIES),
        ("국내도서>사회과학>교육학", BookCategory.SOCIAL_SCIENCE),
        ("국내도서>경제경영>기업 경영", BookCategory.BUSINESS_ECONOMICS),
        ("국내도서>컴퓨터/모바일>프로그래밍", BookCategory.SCIENCE_TECHNOLOGY),
        ("국내도서>어린이>동화/명작/고전", BookCategory.CHILDREN),
        ("국내도서>외국어>영어", BookCategory.LANGUAGE),
        ("국내도서>건강/취미>공예", BookCategory.ART_LIFESTYLE),
    ],
)
def test_maps_aladin_category_path_to_internal_category(
    category_name,
    expected,
):
    assert map_aladin_category(category_name) == expected


def test_prioritizes_parent_category_over_keyword_in_lower_segment():
    assert (
        map_aladin_category("국내도서>인문학>만화로 읽는 철학")
        == BookCategory.HUMANITIES
    )


@pytest.mark.parametrize("category_name", ["", "  ", "국내도서>기타"])
def test_rejects_empty_or_unsupported_category(category_name):
    with pytest.raises(UnsupportedAladinCategoryError):
        map_aladin_category(category_name)


def test_policy_covers_every_internal_book_category():
    assert set(ALADIN_CATEGORY_KEYWORDS) == set(BookCategory)
