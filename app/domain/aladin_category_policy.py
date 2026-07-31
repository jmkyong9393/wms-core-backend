from collections.abc import Mapping, Sequence
from types import MappingProxyType

from app.models.wms import BookCategory


class UnsupportedAladinCategoryError(ValueError):
    pass


# 알라딘 categoryName의 대분류명을 WMS 로케이션 Rack 기준으로 변환한다.
# 먼저 나온 항목을 우선 적용하므로 더 구체적인 키워드를 앞에 둔다.
ALADIN_CATEGORY_KEYWORDS: Mapping[BookCategory, Sequence[str]] = (
    MappingProxyType(
        {
            BookCategory.COMIC: ("만화", "웹툰"),
            BookCategory.STUDY_GUIDE: (
                "수험서",
                "자격증",
                "참고서",
                "문제집",
                "초등학교참고서",
                "중학교참고서",
                "고등학교참고서",
            ),
            BookCategory.NOVEL: (
                "소설",
                "시",
                "희곡",
                "장르소설",
            ),
            BookCategory.HUMANITIES: (
                "인문학",
                "역사",
                "철학",
                "종교",
            ),
            BookCategory.SOCIAL_SCIENCE: (
                "사회과학",
                "정치",
                "법률",
                "교육학",
                "언론",
            ),
            BookCategory.BUSINESS_ECONOMICS: (
                "경제경영",
                "경제",
                "경영",
                "재테크",
            ),
            BookCategory.SCIENCE_TECHNOLOGY: (
                "과학",
                "컴퓨터",
                "모바일",
                "공학",
                "기술",
                "의학",
            ),
            BookCategory.CHILDREN: (
                "어린이",
                "유아",
                "청소년",
            ),
            BookCategory.LANGUAGE: (
                "외국어",
                "어학",
                "국어",
                "사전",
            ),
            BookCategory.ART_LIFESTYLE: (
                "예술",
                "대중문화",
                "건강",
                "취미",
                "레저",
                "여행",
                "요리",
                "가정",
            ),
        }
    )
)


def map_aladin_category(category_name: str) -> BookCategory:
    normalized_path = _normalize_category_path(category_name)
    category_segments = normalized_path.split(">")

    # "국내도서", "외국도서" 같은 상품군은 제외하고 실제 분류부터 비교한다.
    searchable_segments = (
        category_segments[1:]
        if len(category_segments) > 1
        else category_segments
    )
    for segment in searchable_segments:
        for category, keywords in ALADIN_CATEGORY_KEYWORDS.items():
            if any(keyword in segment for keyword in keywords):
                return category

    raise UnsupportedAladinCategoryError(
        f"Unsupported Aladin category: {category_name}"
    )


def _normalize_category_path(category_name: str) -> str:
    normalized_path = "".join(category_name.split())
    if not normalized_path:
        raise UnsupportedAladinCategoryError(
            "Aladin category name must not be empty"
        )
    return normalized_path
