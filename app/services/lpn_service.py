import secrets
from uuid import UUID
from urllib.parse import quote

from app.core.config import settings

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
