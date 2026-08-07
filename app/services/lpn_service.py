import secrets
from datetime import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlmodel import Session

from app.core.config import settings

LABEL_SCAN_PAGE_PREFIX = "/scan"
CERTIFICATE_API_PREFIX = "/api/v1/certificate"
CERTIFICATE_PAGE_PREFIX = "/certificate"

LPN_PREFIX = "LPN"
LPN_COMPANY_CODE = "NZ"
KST = ZoneInfo("Asia/Seoul")


def format_lpn_barcode(
    issued_at: datetime,
    sequence_number: int,
) -> str:
    if sequence_number < 1:
        raise ValueError("LPN sequence number must be positive")

    if issued_at.tzinfo is None:
        issued_at = issued_at.replace(tzinfo=KST)

    issued_date = issued_at.astimezone(KST).strftime("%y%m%d")

    return (
        f"{LPN_PREFIX}-{LPN_COMPANY_CODE}-"
        f"{issued_date}-{sequence_number:06d}"
    )


def generate_lpn_barcode(session: Session) -> str:
    sequence_number = session.execute(
        text("SELECT nextval('lpn_barcode_sequence')")
    ).scalar_one()

    return format_lpn_barcode(
        issued_at=datetime.now(KST),
        sequence_number=int(sequence_number),
    )


def generate_certificate_token() -> str:
    return secrets.token_urlsafe(32)


def build_certificate_api_path(certificate_token: str) -> str:
    encoded_token = quote(certificate_token, safe="")
    return f"{CERTIFICATE_API_PREFIX}/{encoded_token}"


def build_public_qr_url(
    certificate_token: str,
    public_web_base_url: str | None = None,
) -> str:
    base_url = (public_web_base_url or settings.PUBLIC_WEB_BASE_URL).rstrip("/")
    encoded_token = quote(certificate_token, safe="")
    return f"{base_url}{CERTIFICATE_PAGE_PREFIX}/{encoded_token}"


def build_label_scan_qr_url(
    certificate_token: str,
    public_web_base_url: str | None = None,
) -> str:
    """
    실제 LPN·UBCI 라벨 QR에 인코딩할 공통 스캔 페이지 URL을 생성한다.

    작업자 앱은 인증된 내부 LPN 상세 화면으로,
    일반 브라우저는 공개 품질보증서 화면으로 분기한다.
    """
    base_url = (
        public_web_base_url or settings.PUBLIC_WEB_BASE_URL
    ).rstrip("/")
    encoded_token = quote(certificate_token, safe="")

    return (
        f"{base_url}{LABEL_SCAN_PAGE_PREFIX}/{encoded_token}"
    )
