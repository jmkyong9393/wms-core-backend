from datetime import datetime
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi import HTTPException

from app.api.routes import outbound
from app.models.wms import (
    ConditionGrade,
    Inventory,
    InventoryLog,
    InventoryUsedItem,
    Location,
    Order,
    OrderItem,
    OrderItemInventoryAllocation,
    OrderItemLpnAllocation,
    OrderStatus,
    OrderType,
    UsedInventoryStatus,
)


ORDER_ID = UUID("00000000-0000-4000-8000-000000000101")
ORDER_ITEM_ID = UUID("00000000-0000-4000-8000-000000000102")
BOOK_ID = UUID("00000000-0000-4000-8000-000000000103")
INVENTORY_ID = UUID("00000000-0000-4000-8000-000000000104")
USED_INVENTORY_ID = UUID("00000000-0000-4000-8000-000000000105")
LOCATION_ID = UUID("00000000-0000-4000-8000-000000000106")


class FakeQueryResult:
    def __init__(self, *, row=None, rows=None):
        self.row = row
        self.rows = rows or []

    def first(self):
        return self.row

    def all(self):
        return self.rows


class FakeSession:
    def __init__(self, results, locations=None):
        self.results = results
        self.locations = locations or {}
        self.added_items = []
        self.committed = False
        self.rolled_back = False

    def exec(self, statement):
        return self.results.pop(0)

    def get(self, model, object_id):
        if model is Location:
            return self.locations.get(object_id)
        return None

    def add(self, item):
        self.added_items.append(item)

    def commit(self):
        self.committed = True

    def refresh(self, item):
        pass

    def rollback(self):
        self.rolled_back = True


def build_order(
    status=OrderStatus.PICKING,
    waybill_number=None,
    shipping_carrier=None,
    shipped_at=None,
):
    return Order(
        id=ORDER_ID,
        customer_name="출고 확정 테스트 고객",
        type=OrderType.B2B_ORDER,
        total_price=Decimal("15000"),
        status=status,
        waybill_number=waybill_number,
        shipping_carrier=shipping_carrier,
        shipped_at=shipped_at,
    )


def build_order_item():
    return OrderItem(
        id=ORDER_ITEM_ID,
        order_id=ORDER_ID,
        book_id=BOOK_ID,
        quantity=1,
        unit_price=Decimal("15000"),
        final_price=Decimal("15000"),
    )


def build_location():
    return Location(
        id=LOCATION_ID,
        zone="A",
        rack="1",
        shelf="3",
        barcode="A-1-3",
    )


def build_inventory():
    return Inventory(
        id=INVENTORY_ID,
        book_id=BOOK_ID,
        location_id=LOCATION_ID,
        quantity=2,
        reserved_quantity=1,
    )


def build_used_inventory():
    return InventoryUsedItem(
        id=USED_INVENTORY_ID,
        book_id=BOOK_ID,
        location_id=LOCATION_ID,
        lpn_barcode="LPN-SHIPMENT-TEST",
        condition_grade=ConditionGrade.MINT,
        status=UsedInventoryStatus.RESERVED,
    )


def test_confirm_new_stock_shipment_deducts_quantity_and_reservation():
    order = build_order()
    order_item = build_order_item()
    inventory = build_inventory()
    location = build_location()

    allocation = OrderItemInventoryAllocation(
        order_item_id=ORDER_ITEM_ID,
        inventory_id=INVENTORY_ID,
        quantity=1,
        picked_quantity=1,
    )

    book = outbound.Book(
        id=BOOK_ID,
        title="출고 확정 테스트 도서",
        virtual_stock=5,
    )

    session = FakeSession(
        results=[
            FakeQueryResult(row=order),
            FakeQueryResult(rows=[order_item]),
            FakeQueryResult(rows=[allocation]),
            FakeQueryResult(rows=[]),
            FakeQueryResult(row=inventory),
            FakeQueryResult(row=book),
        ],
        locations={LOCATION_ID: location},
    )

    response = outbound.confirm_shipment(
        order_id=ORDER_ID,
        session=session,
    )

    assert response.status == OrderStatus.SHIPPED
    assert response.waybill_number == (
        f"WB-{order.shipped_at:%Y%m%d}-"
        f"{ORDER_ID.hex[:12].upper()}"
    )
    assert response.waybill_barcode == response.waybill_number
    assert response.shipping_carrier == "MOCK_COURIER"
    assert response.shipping_label.is_demo is True
    assert response.shipping_label.title == "TEST / DEMO WAYBILL"
    assert response.shipping_label.sender.name == "Newzed Logistics Center"
    assert response.shipping_label.recipient.name == order.customer_name
    assert response.shipping_label.recipient.phone == "010-1234-5678"
    assert response.shipping_label.recipient.postal_code == "12345"

    assert order.status == OrderStatus.SHIPPED
    assert order.shipped_at is not None
    assert inventory.quantity == 1
    assert inventory.reserved_quantity == 0
    assert book.virtual_stock == 4
    inventory_logs = [
        item
        for item in session.added_items
        if isinstance(item, InventoryLog)
    ]
    assert len(inventory_logs) == 1
    assert (
        inventory_logs[0].condition_grade
        == ConditionGrade.MINT
    )
    assert inventory_logs[0].target_lpn is None
    assert session.committed is True


def test_confirm_used_lpn_shipment_changes_status_to_shipped():
    order = build_order()
    order_item = build_order_item()
    used_inventory = build_used_inventory()
    location = build_location()

    allocation = OrderItemLpnAllocation(
        order_item_id=ORDER_ITEM_ID,
        inventory_used_item_id=USED_INVENTORY_ID,
        picked_at=datetime(2026, 8, 3, 21, 0, 0),
    )

    book = outbound.Book(
        id=BOOK_ID,
        title="중고 출고 확정 테스트 도서",
        virtual_stock=3,
    )

    session = FakeSession(
        results=[
            FakeQueryResult(row=order),
            FakeQueryResult(rows=[order_item]),
            FakeQueryResult(rows=[]),
            FakeQueryResult(rows=[allocation]),
            FakeQueryResult(row=used_inventory),
            FakeQueryResult(row=book),
        ],
        locations={LOCATION_ID: location},
    )

    response = outbound.confirm_shipment(
        order_id=ORDER_ID,
        session=session,
    )

    assert response.status == OrderStatus.SHIPPED
    assert used_inventory.status == UsedInventoryStatus.SHIPPED
    assert book.virtual_stock == 2
    assert session.committed is True


def test_confirm_shipment_returns_existing_waybill_for_shipped_order():
    shipped_at = datetime(2026, 7, 28, 10, 0, 0)
    order = build_order(
        status=OrderStatus.SHIPPED,
        waybill_number="WB-EXISTING-001",
        shipping_carrier="MOCK_COURIER",
        shipped_at=shipped_at,
    )

    session = FakeSession(
        results=[FakeQueryResult(row=order)],
    )

    response = outbound.confirm_shipment(
        order_id=ORDER_ID,
        session=session,
    )

    assert response.status == OrderStatus.SHIPPED
    assert response.waybill_number == "WB-EXISTING-001"
    assert response.waybill_barcode == "WB-EXISTING-001"
    assert response.shipped_at == shipped_at
    assert response.shipping_label.is_demo is True
    assert response.shipping_label.recipient.name == order.customer_name
    assert response.shipping_label.collected_date == shipped_at.date()
    assert session.committed is False


def test_confirm_shipment_rejects_non_picking_order():
    order = build_order(status=OrderStatus.PENDING)

    session = FakeSession(
        results=[FakeQueryResult(row=order)],
    )

    with pytest.raises(HTTPException) as exc_info:
        outbound.confirm_shipment(
            order_id=ORDER_ID,
            session=session,
        )

    assert exc_info.value.status_code == 409
    assert session.rolled_back is True

def test_confirm_shipment_rejects_incomplete_picking():
    order = build_order()
    order_item = build_order_item()

    allocation = OrderItemInventoryAllocation(
        order_item_id=ORDER_ITEM_ID,
        inventory_id=INVENTORY_ID,
        quantity=1,
        picked_quantity=0,
    )

    session = FakeSession(
        results=[
            FakeQueryResult(row=order),
            FakeQueryResult(rows=[order_item]),
            FakeQueryResult(rows=[allocation]),
            FakeQueryResult(rows=[]),
        ],
    )

    with pytest.raises(HTTPException) as exc_info:
        outbound.confirm_shipment(
            order_id=ORDER_ID,
            session=session,
        )

    assert exc_info.value.status_code == 409
    assert (
        exc_info.value.detail["message"]
        == "Shipment cannot be confirmed until all "
        "reserved items are scanned."
    )
    assert (
        exc_info.value.detail["incomplete_new_allocations"][0]
        ["allocation_id"]
        == str(allocation.id)
    )
    assert session.committed is False
    assert session.rolled_back is True
