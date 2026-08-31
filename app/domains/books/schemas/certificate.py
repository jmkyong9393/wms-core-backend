from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.wms import ConditionGrade


class CertificateBookDetail(BaseModel):
    isbn: str | None = Field(description="품질보증서 대상 도서 ISBN")
    title: str = Field(description="품질보증서 대상 도서명")
    publisher: str | None = Field(description="품질보증서 대상 도서 출판사")
    cover_image_url: str | None = Field(
        default=None,
        description="도서 표지 이미지 URL",
    )


class CertificateResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "book": {
                    "isbn": "9781234567890",
                    "title": "사피엔스",
                    "publisher": "김영사",
                },
                "cover_image_url": "https://example.com/book-cover.jpg",
                "condition_grade": "EXCELLENT",
                "ubci_score": "92.00",
                "report_summary": (
                    "경미한 모서리 찍힘이 있으나 전체 상태는 우수합니다."
                ),
                "inspected_at": "2026-07-27T10:00:00",
            }
        }
    )

    book: CertificateBookDetail = Field(
        description="소비자가 확인할 도서 기본 정보",
    )
    condition_grade: ConditionGrade = Field(
        description="검수 완료 후 확정된 품질 등급",
    )
    ubci_score: Decimal = Field(
        ge=0,
        le=100,
        description="AI 검수로 확정된 UBCI 품질 점수",
    )
    report_summary: str = Field(
        min_length=1,
        description="소비자에게 공개할 품질 검수 결과 요약",
    )
    inspected_at: datetime = Field(
        description="현재 보증서 결과가 확정된 검수 시각",
    )
