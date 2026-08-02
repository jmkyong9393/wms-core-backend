import secrets
from uuid import UUID
from urllib.parse import quote

from app.core.config import settings

LABEL_SCAN_PAGE_PREFIX = "/scan"
LPN_PREFIX = "LPN"
CERTIFICATE_API_PREFIX = "/api/v1/certificate"
CERTIFICATE_PAGE_PREFIX = "/certificate"


def generate_lpn_barcode(inbound_item_id: UUID) -> str:
    return f"{LPN_PREFIX}-{inbound_item_id.hex.upper()}"  # LPN 네이밍 규칙은 아직 미정. 변경될 여지 큼.


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
