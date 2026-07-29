from decimal import Decimal
from uuid import uuid4

from app.models.wms import (
    BookCategory,
    ConditionGrade,
    InboundStatus,
    InboundType,
    PutawayStatus,
)
from app.schemas.new_stock_inbound import (
    NewStockInboundRequest,
    NewStockInboundResponse,
)


def test_new_stock_request_is_single_book_without_location_or_quantity():
    request = NewStockInboundRequest(
        isbn="9788912345678",
        title="신간 소설",
        category=BookCategory.NOVEL,
        base_price=Decimal("15000.00"),
        supplier_name="교보문고",
    )

    assert request.isbn == "9788912345678"
    assert request.category == BookCategory.NOVEL
    assert "quantity" not in NewStockInboundRequest.model_fields
    assert "location_barcode" not in NewStockInboundRequest.model_fields


def test_new_stock_response_exposes_lpn_and_assigned_location():
    response = NewStockInboundResponse(
        inbound_id=uuid4(),
        inbound_item_id=uuid4(),
        inbound_type=InboundType.NEW_STOCK,
        status=InboundStatus.RECEIVED,
        book_id=uuid4(),
        condition_grade=ConditionGrade.NEW,
        lpn_barcode="LPN-TEST-NEW",
        certificate_url="https://wms.example.com/certificate/token",
        putaway_status=PutawayStatus.WAITING,
        location_id=uuid4(),
        location_barcode="A-3-1",
    )

    assert response.condition_grade == ConditionGrade.NEW
    assert response.putaway_status == PutawayStatus.WAITING
    assert response.location_barcode == "A-3-1"
