from fastapi import APIRouter, Depends, Path
from sqlmodel import Session

from app.core.database import get_session
from app.domains.books.certificate_service import get_certificate_by_token
from app.domains.books.schemas.certificate import CertificateResponse

router = APIRouter()


@router.get(
    "/{token}",
    response_model=CertificateResponse,
    operation_id="getCertificateByToken",
    summary="공개 토큰 기반 UBCI 품질보증서 조회",
    description=(
        "LPN 라벨의 QR에 포함된 공개 토큰으로 품질보증서를 조회합니다. "
        "DB에 저장된 최신 확정 검수 결과를 반환하며, 인증 없이 접근할 수 있는 "
        "소비자 공개 API입니다. LPN, 로케이션, 내부 작업 로그는 노출하지 않습니다."
    ),
    responses={
        404: {
            "description": "조회 가능하거나 발급이 완료된 품질보증서가 없음",
            "content": {"application/json": {"example": {"detail": "Certificate not found"}}},
        }
    },
)
def get_certificate(
    token: str = Path(
        min_length=32,
        max_length=128,
        description="입고 시 발급된 공개 품질보증서 토큰",
        examples=["m7sX0zYV2wF6U3pG8nR4cQ1aK9tB5eHjL0dSxWvNqPo"],
    ),
    session: Session = Depends(get_session),
) -> CertificateResponse:
    return get_certificate_by_token(session, token)
