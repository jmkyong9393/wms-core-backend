from decimal import Decimal
from uuid import UUID

import pytest
from fastapi import HTTPException

from app.api.routes import outbound
from app.models.wms import (
    ConditionGrade,
    Inventory,
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
from app.services.fifo_lpn_service import FifoLpnCandidate


ORDER_ID = UUID("00000000-0000-4000-8000-000000000001")
ORDER_ITEM_ID = UUID("00000000-0000-4000-8000-000000000002")
BOOK_ID = UUID("00000000-0000-4000-8000-000000000003")
INVENTORY_ID = UUID("00000000-0000-4000-8000-000000000004")
USED_INVENTORY_ID = UUID("00000000-0000-4000-8000-000000000005")
LOCATION_ID = UUID("00000000-0000-4000-8000-000000000006")


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


def build_order(status=OrderStatus.PENDING):
    return Order(
        id=ORDER_ID,
        customer_name="피킹 테스트 고객",
        type=OrderType.B2B_ORDER,
        total_price=Decimal("15000"),
        status=status,
    )


def build_order_item(
    condition_grade: ConditionGrade | None = None,
    quantity: int = 1,
):
    return OrderItem(
        id=ORDER_ITEM_ID,
        order_id=ORDER_ID,
        book_id=BOOK_ID,
        quantity=quantity,
        unit_price=Decimal("15000"),
        final_price=Decimal("15000") * quantity,
        condition_grade=condition_grade,
    )


def build_location(
    zone="A",
    rack="1",
    shelf="3",
):
    return Location(
        id=LOCATION_ID,
        zone=zone,
        rack=rack,
        shelf=shelf,
        barcode=f"{zone}-{rack}-{shelf}",
    )


def build_inventory(
    quantity=3,
    reserved_quantity=0,
    inventory_id=INVENTORY_ID,
    discount_rate=Decimal("0.1000"),
    sale_price=Decimal("13500.00"),
):
    return Inventory(
        id=inventory_id,
        book_id=BOOK_ID,
        location_id=LOCATION_ID,
        quantity=quantity,
        reserved_quantity=reserved_quantity,
        discount_rate=discount_rate,
        sale_price=sale_price,
    )


def build_used_inventory():
    return InventoryUsedItem(
        id=USED_INVENTORY_ID,
        book_id=BOOK_ID,
        location_id=LOCATION_ID,
        lpn_barcode="LPN-TEST-0001",
        ubci_score=Decimal("97.00"),
        discount_rate=Decimal("0.1000"),
        sale_price=Decimal("13500.00"),
        condition_grade=ConditionGrade.MINT,
        status=UsedInventoryStatus.AVAILABLE,
    )


def test_build_picking_groups_sorts_by_zone_rack_and_shelf():
    location_a_2_1 = build_location(zone="A", rack="2", shelf="1")
    location_a_1_3 = build_location(zone="A", rack="1", shelf="3")
    location_b_1_1 = build_location(zone="B", rack="1", shelf="1")

    item = outbound.PickingInstructionItem(
        order_item_id=ORDER_ITEM_ID,
        book_id=BOOK_ID,
        quantity=1,
        condition_grade=ConditionGrade.MINT,
    )

    groups = outbound.build_picking_groups(
        [
            (location_b_1_1, item),
            (location_a_2_1, item),
            (location_a_1_3, item),
        ]
    )

    assert [group.zone for group in groups] == ["A", "B"]
    assert [rack.rack for rack in groups[0].racks] == ["1", "2"]
    assert groups[0].racks[0].shelves[0].shelf == "3"


def test_new_stock_picking_reserves_inventory_without_deducting_quantity():
    order = build_order()
    order_item = build_order_item(quantity=2)
    inventory = build_inventory(quantity=3)
    location = build_location()

    session = FakeSession(
        results=[
            FakeQueryResult(row=order),
            FakeQueryResult(rows=[order_item]),
            FakeQueryResult(rows=[inventory]),
        ],
        locations={LOCATION_ID: location},
    )

    response = outbound.create_picking_instruction(
        outbound.PickRequest(order_id=ORDER_ID),
        session=session,
    )

    allocations = [
        item
        for item in session.added_items
        if isinstance(item, OrderItemInventoryAllocation)
    ]

    assert response.status == OrderStatus.PICKING
    assert response.total_price == Decimal("27000.00")
    assert order.status == OrderStatus.PICKING
    assert inventory.quantity == 3
    assert inventory.reserved_quantity == 2
    assert order_item.unit_price == Decimal("13500.00")
    assert order_item.final_price == Decimal("27000.00")
    assert len(allocations) == 1
    assert allocations[0].order_item_id == ORDER_ITEM_ID
    assert allocations[0].inventory_id == INVENTORY_ID
    assert allocations[0].quantity == 2
    picked_item = response.picking_groups[0].racks[0].shelves[0].items[0]
    assert picked_item.condition_grade == ConditionGrade.MINT
    assert picked_item.lpn_barcode is None
    assert session.committed is True


def test_used_lpn_picking_reserves_lpn_and_creates_allocation(monkeypatch):
    order = build_order()
    order_item = build_order_item(
        condition_grade=ConditionGrade.MINT,
    )
    used_inventory = build_used_inventory()
    location = build_location()

    candidate = FifoLpnCandidate(
        inventory_used_item=used_inventory,
        picked_location=location.barcode,
    )

    monkeypatch.setattr(
        outbound,
        "select_fifo_lpn_candidate",
        lambda **kwargs: candidate,
    )

    session = FakeSession(
        results=[
            FakeQueryResult(row=order),
            FakeQueryResult(rows=[order_item]),
        ],
        locations={LOCATION_ID: location},
    )

    response = outbound.create_picking_instruction(
        outbound.PickRequest(order_id=ORDER_ID),
        session=session,
    )

    allocations = [
        item
        for item in session.added_items
        if isinstance(item, OrderItemLpnAllocation)
    ]

    assert response.status == OrderStatus.PICKING
    assert response.total_price == Decimal("13500.00")
    assert used_inventory.status == UsedInventoryStatus.RESERVED
    assert len(allocations) == 1
    assert allocations[0].order_item_id == ORDER_ITEM_ID
    assert allocations[0].inventory_used_item_id == USED_INVENTORY_ID
    assert order_item.unit_price == Decimal("15000")
    assert order_item.final_price == Decimal("13500.00")
    assert order.total_price == Decimal("13500.00")
    assert session.committed is True


def test_picking_recalculates_total_from_new_and_used_order_items(monkeypatch):
    order = build_order()
    new_order_item = build_order_item(quantity=2)
    used_order_item = build_order_item(
        condition_grade=ConditionGrade.MINT,
    )
    used_order_item.id = UUID("00000000-0000-4000-8000-000000000007")
    new_order_item.final_price = Decimal("30000.00")
    used_inventory = build_used_inventory()
    location = build_location()
    inventory = build_inventory(quantity=2)

    monkeypatch.setattr(
        outbound,
        "select_fifo_lpn_candidate",
        lambda **kwargs: FifoLpnCandidate(
            inventory_used_item=used_inventory,
            picked_location=location.barcode,
        ),
    )

    session = FakeSession(
        results=[
            FakeQueryResult(row=order),
            FakeQueryResult(rows=[new_order_item, used_order_item]),
            FakeQueryResult(rows=[inventory]),
        ],
        locations={LOCATION_ID: location},
    )

    outbound.create_picking_instruction(
        outbound.PickRequest(order_id=ORDER_ID),
        session=session,
    )

    assert new_order_item.final_price == Decimal("27000.00")
    assert used_order_item.final_price == Decimal("13500.00")
    assert order.total_price == Decimal("40500.00")


def test_new_stock_picking_uses_fifo_inventory_prices_by_quantity():
    order = build_order()
    order_item = build_order_item(quantity=3)
    first_inventory = build_inventory(
        quantity=2,
        sale_price=Decimal("13500.00"),
    )
    second_inventory = build_inventory(
        quantity=2,
        inventory_id=UUID("00000000-0000-4000-8000-000000000008"),
        discount_rate=Decimal("0.2000"),
        sale_price=Decimal("12000.00"),
    )
    location = build_location()

    session = FakeSession(
        results=[
            FakeQueryResult(row=order),
            FakeQueryResult(rows=[order_item]),
            FakeQueryResult(rows=[first_inventory, second_inventory]),
        ],
        locations={LOCATION_ID: location},
    )

    response = outbound.create_picking_instruction(
        outbound.PickRequest(order_id=ORDER_ID),
        session=session,
    )

    assert first_inventory.reserved_quantity == 2
    assert second_inventory.reserved_quantity == 1
    assert order_item.final_price == Decimal("39000.00")
    assert order_item.unit_price == Decimal("13000.00")
    assert order.total_price == Decimal("39000.00")
    assert response.total_price == Decimal("39000.00")


def test_new_stock_picking_rejects_inventory_without_pricing():
    order = build_order()
    order_item = build_order_item(quantity=1)
    inventory = build_inventory(
        quantity=1,
        discount_rate=None,
        sale_price=None,
    )
    location = build_location()
    session = FakeSession(
        results=[
            FakeQueryResult(row=order),
            FakeQueryResult(rows=[order_item]),
            FakeQueryResult(rows=[inventory]),
        ],
        locations={LOCATION_ID: location},
    )

    with pytest.raises(HTTPException) as exc_info:
        outbound.create_picking_instruction(
            outbound.PickRequest(order_id=ORDER_ID),
            session=session,
        )

    assert exc_info.value.status_code == 409
    assert session.rolled_back is True


def test_picking_instruction_rejects_non_pending_order():
    order = build_order(status=OrderStatus.PICKING)
    session = FakeSession(
        results=[FakeQueryResult(row=order)],
    )

    with pytest.raises(HTTPException) as exc_info:
        outbound.create_picking_instruction(
            outbound.PickRequest(order_id=ORDER_ID),
            session=session,
        )

    assert exc_info.value.status_code == 409
    assert session.rolled_back is True


def test_insufficient_inventory_rolls_back_without_reservation():
    order = build_order()
    order_item = build_order_item(quantity=1)
    inventory = build_inventory(
        quantity=1,
        reserved_quantity=1,
    )
    location = build_location()

    session = FakeSession(
        results=[
            FakeQueryResult(row=order),
            FakeQueryResult(rows=[order_item]),
            FakeQueryResult(rows=[inventory]),
        ],
        locations={LOCATION_ID: location},
    )

    with pytest.raises(HTTPException) as exc_info:
        outbound.create_picking_instruction(
            outbound.PickRequest(order_id=ORDER_ID),
            session=session,
        )

    assert exc_info.value.status_code == 409
    assert order.status == OrderStatus.PENDING
    assert inventory.reserved_quantity == 1
    assert session.committed is False
    assert session.rolled_back is True
