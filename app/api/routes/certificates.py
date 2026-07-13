from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class CertificateResponse(BaseModel):
    book_id: UUID
    ubci_score: int
    report_summary: str
    qr_code_url: str


@router.get("/{id}", response_model=CertificateResponse)
def get_certificate(id: UUID):
    return CertificateResponse(
        book_id=UUID("00000000-0000-0000-0000-000000000001"),
        ubci_score=95,
        report_summary="낙서 없음, 상태 우수",
        qr_code_url=f"https://example.com/certificate/{id}",
    )
