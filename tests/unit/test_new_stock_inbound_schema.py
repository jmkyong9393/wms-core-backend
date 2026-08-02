from decimal import Decimal
from uuid import uuid4

from app.models.wms import (
    BookCategory,
    InboundStatus,
    InboundType,
)
from app.schemas.new_stock_inbound import (
    NewStockInboundRequest,
    NewStockInboundResponse,
)


def test_new_stock_request_accepts_isbn_quantity_without_location():
    request = NewStockInboundRequest(
        isbn="9788912345678",
        title="신간 소설",
        category=BookCategory.NOVEL,
        base_price=Decimal("15000.00"),
        quantity=10,
        supplier_name="교보문고",
    )

    assert request.isbn == "9788912345678"
    assert request.category == BookCategory.NOVEL
    assert request.quantity == 10
    assert "location_barcode" not in NewStockInboundRequest.model_fields


def test_new_stock_response_exposes_quantity_and_assigned_location():
    response = NewStockInboundResponse(
        inbound_id=uuid4(),
        inbound_item_id=uuid4(),
        inbound_type=InboundType.NEW_STOCK,
        status=InboundStatus.RECEIVED,
        book_id=uuid4(),
        received_quantity=10,
        location_id=uuid4(),
        location_barcode="A-3-1",
        inventory_id=uuid4(),
        inventory_quantity=10,
    )

    assert response.received_quantity == 10
    assert response.inventory_quantity == 10
    assert "lpn_barcode" not in NewStockInboundResponse.model_fields
    assert "condition_grade" not in NewStockInboundResponse.model_fields
    assert response.location_barcode == "A-3-1"
