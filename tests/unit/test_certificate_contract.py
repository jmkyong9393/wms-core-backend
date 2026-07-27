from app.api.routes.certificates import get_certificate


def test_certificate_lookup_accepts_public_token():
    certificate_token = "public-certificate-token"

    response = get_certificate(certificate_token)

    assert (
        response.qr_code_url
        == f"/api/v1/certificate/{certificate_token}"
    )
