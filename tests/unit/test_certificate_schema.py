from datetime import datetime

from app.schemas.certificate import CertificateResponse


def test_certificate_response_preserves_public_quality_data():
    response = CertificateResponse(
        book={
            "isbn": "9781234567890",
            "title": "사피엔스",
            "publisher": "김영사",
        },
        condition_grade="EXCELLENT",
        report_summary="경미한 손상이 있으나 전체 상태는 우수합니다.",
        inspected_at=datetime(2026, 7, 27, 10, 0, 0),
    )

    serialized = response.model_dump(mode="json")

    assert serialized["condition_grade"] == "EXCELLENT"
    assert "ubci_score" not in serialized
    assert "lpn_barcode" not in serialized
    assert "location" not in serialized
    assert "agent_logs" not in serialized
