from datetime import datetime
from decimal import Decimal
from uuid import UUID

from app.domains.lpn.schemas.lpn import LpnDetailResponse


def test_lpn_detail_response_preserves_inventory_metadata():
    response = LpnDetailResponse(
        lpn_barcode="LPN-1234",
        book={
            "id": UUID("00000000-0000-4000-8000-000000000001"),
            "isbn": "9781234567890",
            "title": "사피엔스",
            "publisher": "김영사",
        },
        inventory_status="AVAILABLE",
        condition_grade="EXCELLENT",
        ubci_score=Decimal("91.50"),
        base_price=Decimal("18000.00"),
        discount_rate=Decimal("0.1500"),
        sale_price=Decimal("15300.00"),
        pricing_status="AGENT_PRICED",
        location={
            "id": UUID("00000000-0000-4000-8000-000000000002"),
            "barcode": "A-1-3",
            "zone": "A",
            "rack": "1",
            "shelf": "3",
        },
        stocked_at=datetime(2026, 7, 27, 10, 0, 0),
        certificate_url=(
            "https://wms.example.com/certificate/public-certificate-token"
        ),
    )

    assert response.ubci_score == Decimal("91.50")
    assert response.sale_price == Decimal("15300.00")
    assert response.pricing_status == "AGENT_PRICED"
    assert response.location.barcode == "A-1-3"
    assert response.model_dump(mode="json")["ubci_score"] == "91.50"
