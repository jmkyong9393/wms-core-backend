from datetime import datetime
from decimal import Decimal
from uuid import UUID

from app.models.wms import (
    Order,
    OrderItem,
    OrderItemInventoryAllocation,
    OrderItemLpnAllocation,
    OrderStatus,
    OrderType,
)
from app.domains.admin.outbound_dashboard_service import (
    get_outbound_dashboard_summary,
)


PICKING_ORDER_ID = UUID("00000000-0000-4000-8000-000000000101")
SHIPPED_ORDER_ID = UUID("00000000-0000-4000-8000-000000000102")
NEW_ORDER_ITEM_ID = UUID("00000000-0000-4000-8000-000000000103")
USED_ORDER_ITEM_ID = UUID("00000000-0000-4000-8000-000000000104")
BOOK_ID = UUID("00000000-0000-4000-8000-000000000105")
INVENTORY_ID = UUID("00000000-0000-4000-8000-000000000106")
USED_INVENTORY_ID = UUID("00000000-0000-4000-8000-000000000107")


class FakeQueryResult:
    def __init__(self, rows=None):
        self.rows = rows or []

    def all(self):
        return self.rows


class FakeSession:
    def __init__(self, results):
        self.results = results
        self.statements = []

    def exec(self, statement):
        self.statements.append(statement)
        return self.results.pop(0)


def build_order(
    *,
    order_id: UUID,
    status: OrderStatus,
    customer_name: str,
    shipped_at: datetime | None = None,
    waybill_number: str | None = None,
) -> Order:
    return Order(
        id=order_id,
        customer_name=customer_name,
        type=OrderType.B2B_ORDER,
        total_price=Decimal("30000"),
        status=status,
        shipped_at=shipped_at,
        waybill_number=waybill_number,
        created_at=datetime(2026, 8, 4, 9, 0, 0),
    )


def test_calculates_picking_progress_and_today_shipping_count():
    now = datetime.utcnow()

    picking_order = build_order(
        order_id=PICKING_ORDER_ID,
        status=OrderStatus.PICKING,
        customer_name="피킹 고객사",
    )
    shipped_order = build_order(
        order_id=SHIPPED_ORDER_ID,
        status=OrderStatus.SHIPPED,
        customer_name="출고 완료 고객사",
        shipped_at=now,
        waybill_number="WB-20260804-ABCDEF123456",
    )

    new_order_item = OrderItem(
        id=NEW_ORDER_ITEM_ID,
        order_id=PICKING_ORDER_ID,
        book_id=BOOK_ID,
        quantity=4,
        unit_price=Decimal("10000"),
        final_price=Decimal("40000"),
    )
    used_order_item = OrderItem(
        id=USED_ORDER_ITEM_ID,
        order_id=PICKING_ORDER_ID,
        book_id=BOOK_ID,
        quantity=1,
        unit_price=Decimal("10000"),
        final_price=Decimal("10000"),
    )

    new_allocation = OrderItemInventoryAllocation(
        order_item_id=NEW_ORDER_ITEM_ID,
        inventory_id=INVENTORY_ID,
        quantity=4,
        picked_quantity=3,
    )
    used_allocation = OrderItemLpnAllocation(
        order_item_id=USED_ORDER_ITEM_ID,
        inventory_used_item_id=USED_INVENTORY_ID,
        picked_at=now,
    )

    session = FakeSession(
        results=[
            FakeQueryResult(rows=[picking_order]),
            FakeQueryResult(rows=[new_order_item, used_order_item]),
            FakeQueryResult(rows=[new_allocation]),
            FakeQueryResult(rows=[used_allocation]),
            FakeQueryResult(rows=[shipped_order]),
            FakeQueryResult(rows=[picking_order, shipped_order]),
        ]
    )

    response = get_outbound_dashboard_summary(session)

    assert response.active_picking_order_count == 1
    assert response.picking_completion_rate == 80.0
    assert response.today_shipping_label_issued_count == 1
    assert [order.id for order in response.recent_orders] == [
        PICKING_ORDER_ID,
        SHIPPED_ORDER_ID,
    ]


def test_returns_zero_rate_when_no_active_picking_orders():
    session = FakeSession(
        results=[
            FakeQueryResult(rows=[]),
            FakeQueryResult(rows=[]),
            FakeQueryResult(rows=[]),
        ]
    )

    response = get_outbound_dashboard_summary(session)

    assert response.active_picking_order_count == 0
    assert response.picking_completion_rate == 0.0
    assert response.today_shipping_label_issued_count == 0
    assert response.recent_orders == []
