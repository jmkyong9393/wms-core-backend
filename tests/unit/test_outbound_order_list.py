from datetime import datetime
from decimal import Decimal
from uuid import UUID

from app.domains.orders import orders
from app.models.wms import Order, OrderStatus, OrderType


FIRST_ORDER_ID = UUID("00000000-0000-4000-8000-000000000101")
SECOND_ORDER_ID = UUID("00000000-0000-4000-8000-000000000102")


class FakeQueryResult:
    def __init__(self, *, one_value=None, rows=None):
        self.one_value = one_value
        self.rows = rows or []

    def one(self):
        return self.one_value

    def all(self):
        return self.rows


class FakeSession:
    def __init__(self, results):
        self.results = results

    def exec(self, statement):
        return self.results.pop(0)


def build_order(
    order_id: UUID,
    customer_name: str,
    created_at: datetime,
) -> Order:
    return Order(
        id=order_id,
        customer_name=customer_name,
        type=OrderType.B2B_ORDER,
        total_price=Decimal("30000"),
        status=OrderStatus.PENDING,
        logistics_center="SEOUL_DC",
        created_at=created_at,
    )


def test_lists_pending_outbound_orders_with_pagination():
    first_order = build_order(
        FIRST_ORDER_ID,
        "첫 번째 고객",
        datetime(2026, 8, 3, 22, 0, 0),
    )
    second_order = build_order(
        SECOND_ORDER_ID,
        "두 번째 고객",
        datetime(2026, 8, 3, 21, 0, 0),
    )

    session = FakeSession(
        results=[
            FakeQueryResult(one_value=2),
            FakeQueryResult(rows=[first_order]),
        ]
    )

    response = orders.list_outbound_orders(
        status_filter=OrderStatus.PENDING,
        page=1,
        size=1,
        session=session,
    )

    assert response.total == 2
    assert response.page == 1
    assert response.size == 1
    assert response.total_pages == 2
    assert len(response.items) == 1
    assert response.items[0].id == FIRST_ORDER_ID
    assert response.items[0].customer_name == "첫 번째 고객"
    assert response.items[0].status == OrderStatus.PENDING


def test_returns_zero_total_pages_when_no_outbound_orders_exist():
    session = FakeSession(
        results=[
            FakeQueryResult(one_value=0),
            FakeQueryResult(rows=[]),
        ]
    )

    response = orders.list_outbound_orders(
        status_filter=OrderStatus.PENDING,
        page=1,
        size=20,
        session=session,
    )

    assert response.items == []
    assert response.total == 0
    assert response.total_pages == 0