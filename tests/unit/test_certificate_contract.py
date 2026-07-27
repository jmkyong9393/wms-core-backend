from app.api.routes.certificates import get_certificate


def test_certificate_lookup_accepts_lpn_barcode():
    lpn_barcode = "LPN-12345678123456781234567812345678"

    response = get_certificate(lpn_barcode)

    assert response.qr_code_url == f"/api/v1/certificate/{lpn_barcode}"
