from uuid import UUID


LPN_PREFIX = "LPN"
CERTIFICATE_API_PREFIX = "/api/v1/certificate"


def generate_lpn_barcode(inbound_item_id: UUID) -> str:
    return f"{LPN_PREFIX}-{inbound_item_id.hex.upper()}"  # LPN 네이밍 규칙은 아직 미정. 변경될 여지 큼.


def build_certificate_url(lpn_barcode: str) -> str:
    return f"{CERTIFICATE_API_PREFIX}/{lpn_barcode}"
