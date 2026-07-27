from uuid import UUID

import pytest
from pydantic import ValidationError

from app.schemas.used_inbound import UsedBookInboundRequest
from app.services.lpn_service import (
    build_certificate_api_path,
    build_public_qr_url,
    generate_lpn_barcode,
)


def test_generate_lpn_barcode_from_inbound_item_id():
    inbound_item_id = UUID("12345678-1234-5678-1234-567812345678")

    assert (
        generate_lpn_barcode(inbound_item_id)
        == "LPN-12345678123456781234567812345678"
    )


def test_build_certificate_api_path_from_lpn_barcode():
    lpn_barcode = "LPN-12345678123456781234567812345678"

    assert (
        build_certificate_api_path(lpn_barcode)
        == f"/api/v1/certificate/{lpn_barcode}"
    )


def test_build_public_qr_url_from_lpn_barcode():
    lpn_barcode = "LPN-12345678123456781234567812345678"

    assert build_public_qr_url(
        lpn_barcode,
        public_web_base_url="https://wms.example.com/",
    ) == f"https://wms.example.com/certificate/{lpn_barcode}"


@pytest.mark.parametrize("inbound_type", ["USED_PURCHASE", "CUSTOMER_RETURN"])
def test_used_inbound_request_accepts_inspection_types(inbound_type):
    request = UsedBookInboundRequest(
        inbound_type=inbound_type,
        book_id="00000000-0000-4000-8000-000000000001",
    )

    assert request.inbound_type.value == inbound_type


def test_used_inbound_request_rejects_new_stock():
    with pytest.raises(ValidationError):
        UsedBookInboundRequest(
            inbound_type="NEW_STOCK",
            book_id="00000000-0000-4000-8000-000000000001",
        )
