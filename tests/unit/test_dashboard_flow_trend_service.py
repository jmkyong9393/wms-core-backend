from datetime import datetime, timedelta
from types import SimpleNamespace

from app.models.wms import (
    InventoryTransactionType,
)
from app.domains.admin.dashboard_flow_trend_service import (
    get_dashboard_flow_trend,
)


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class FakeSession:
    def __init__(self, results):
        self.results = results
        self.statements = []

    def exec(self, statement):
        self.statements.append(statement)
        return self.results.pop(0)


def test_returns_daily_inventory_and_inspection_flow_with_empty_dates():
    today = datetime.utcnow().date()
    first_day = today - timedelta(days=2)

    inventory_logs = [
        SimpleNamespace(
            created_at=datetime.combine(
                first_day,
                datetime.min.time(),
            ),
            transaction_type=InventoryTransactionType.INBOUND,
            quantity_change=5,
        ),
        SimpleNamespace(
            created_at=datetime.combine(
                first_day,
                datetime.min.time(),
            ),
            transaction_type=InventoryTransactionType.OUTBOUND,
            quantity_change=-2,
        ),
        SimpleNamespace(
            created_at=datetime.combine(
                today,
                datetime.min.time(),
            ),
            transaction_type=InventoryTransactionType.INBOUND,
            quantity_change=3,
        ),
    ]

    first_started_at = datetime.combine(
        first_day,
        datetime.min.time(),
    )
    today_started_at = datetime.combine(
        today,
        datetime.min.time(),
    )

    completed_jobs = [
        SimpleNamespace(
            ai_inspection_started_at=first_started_at,
            ai_inspection_completed_at=(
                first_started_at + timedelta(seconds=60)
            ),
        ),
        SimpleNamespace(
            ai_inspection_started_at=today_started_at,
            ai_inspection_completed_at=(
                today_started_at + timedelta(seconds=120)
            ),
        ),
    ]

    session = FakeSession(
        results=[
            FakeResult(inventory_logs),
            FakeResult(completed_jobs),
        ]
    )

    response = get_dashboard_flow_trend(
        session=session,
        days=3,
    )

    assert response.days == 3
    assert len(response.items) == 3

    assert response.items[0].date == first_day
    assert response.items[0].inbound_quantity == 5
    assert response.items[0].outbound_quantity == 2
    assert response.items[0].average_inspection_processing_seconds == 60

    assert response.items[1].inbound_quantity == 0
    assert response.items[1].outbound_quantity == 0
    assert response.items[1].average_inspection_processing_seconds == 0

    assert response.items[2].date == today
    assert response.items[2].inbound_quantity == 3
    assert response.items[2].outbound_quantity == 0
    assert response.items[2].average_inspection_processing_seconds == 120

    assert len(session.statements) == 2