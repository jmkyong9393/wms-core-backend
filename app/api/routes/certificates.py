from uuid import UUID

from fastapi import APIRouter, Path
from pydantic import BaseModel, Field

from app.services.lpn_service import build_certificate_api_path

router = APIRouter()


class CertificateResponse(BaseModel):
    book_id: UUID = Field(description="보증서 대상 도서 마스터 ID")
    ubci_score: float = Field(description="검수 완료 후 확정된 UBCI 점수")
    report_summary: str = Field(description="품질 검수 결과 요약")
    qr_code_url: str = Field(description="이 보증서를 조회하는 공개 토큰 기반 경로")


@router.get(
    "/{token}",
    response_model=CertificateResponse,
    operation_id="getCertificateByToken",
    summary="공개 토큰 기반 UBCI 품질보증서 조회",
    description=(
        "LPN 라벨의 QR에 포함된 공개 토큰으로 품질보증서를 조회합니다. "
        "현재 응답은 "
        "프론트엔드 연동용 mock이며, 실제 보증서 DB 조회는 BE-3.10에서 "
        "구현합니다."
    ),
)
def get_certificate(
    token: str = Path(
        description="입고 시 발급된 공개 품질보증서 토큰",
        examples=["m7sX0zYV2wF6U3pG8nR4cQ1aK9tB5eHjL0dSxWvNqPo"],
    ),
):
    return CertificateResponse(
        book_id=UUID("00000000-0000-0000-0000-000000000001"),
        ubci_score=95,
        report_summary="낙서 없음, 상태 우수",
        qr_code_url=build_certificate_api_path(token),
    )
