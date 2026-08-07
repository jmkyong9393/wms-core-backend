from uuid import uuid4
from datetime import datetime, time, timedelta
from types import SimpleNamespace

from app.models.wms import (
    ConditionGrade,
    InboundStatus,
    InboundType,
    InventoryTransactionType,
    ReturnJobStatus,
)
from app.services.inbound_dashboard_service import (
    get_inbound_dashboard_summary,
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


def test_returns_inbound_dashboard_summary():
    now = datetime.utcnow()
    today = now.date()
    first_day = today - timedelta(days=6)

    inbound_logs = [
        SimpleNamespace(
            created_at=datetime.combine(first_day, time.min),
            transaction_type=InventoryTransactionType.INBOUND,
            quantity_change=3,
            target_lpn=None,
        ),
        SimpleNamespace(
            created_at=datetime.combine(today, time.min),
            transaction_type=InventoryTransactionType.INBOUND,
            quantity_change=2,
            target_lpn="LPN-USED-001",
        ),
    ]

    session = FakeSession(
        results=[
            FakeResult(inbound_logs),
            FakeResult([SimpleNamespace(), SimpleNamespace()]),
            FakeResult([SimpleNamespace()]),
            FakeResult([SimpleNamespace()]),
            FakeResult(
                [
                    SimpleNamespace(
                        condition_grade=ConditionGrade.EXCELLENT,
                    ),
                    SimpleNamespace(
                        condition_grade=ConditionGrade.EXCELLENT,
                    ),
                    SimpleNamespace(
                        condition_grade=ConditionGrade.NORMAL,
                    ),
                ]
            ),
            FakeResult(
                [
                    (
                        SimpleNamespace(
                            quantity=10,
                            reserved_quantity=3,
                        ),
                        SimpleNamespace(zone="A"),
                    ),
                    (
                        SimpleNamespace(
                            quantity=4,
                            reserved_quantity=1,
                        ),
                        SimpleNamespace(zone="B"),
                    ),
                ]
            ),
            FakeResult(
                [
                    (
                        SimpleNamespace(),
                        SimpleNamespace(zone="B"),
                    ),
                    (
                        SimpleNamespace(),
                        SimpleNamespace(zone="B"),
                    ),
                ]
            ),
            FakeResult(
                [
                    (
                        SimpleNamespace(
                            id=uuid4(),
                            quantity=1,
                            created_at=datetime.combine(today, time.min),
                        ),
                        SimpleNamespace(
                            inbound_type=InboundType.CUSTOMER_RETURN,
                            status=InboundStatus.COMPLETED,
                        ),
                        SimpleNamespace(title="입고 대시보드 테스트 도서"),
                        SimpleNamespace(barcode="B-2-1"),
                    )
                ]
            ),
        ]
    )

    response = get_inbound_dashboard_summary(
        session=session,
        days=7,
    )

    assert response.today_inbound_quantity == 2
    assert response.completed_inspection_count == 1
    assert response.pending_inspection_count == 2
    assert response.recheck_required_count == 1

    assert len(response.daily_inbound_trend) == 7
    assert response.daily_inbound_trend[0].date == first_day
    assert response.daily_inbound_trend[0].new_stock_quantity == 3
    assert response.daily_inbound_trend[0].used_return_quantity == 0
    assert response.daily_inbound_trend[-1].date == today
    assert response.daily_inbound_trend[-1].new_stock_quantity == 0
    assert response.daily_inbound_trend[-1].used_return_quantity == 2

    grade_counts = {
        item.grade: item.quantity
        for item in response.grade_distribution
    }
    assert grade_counts[ConditionGrade.EXCELLENT] == 2
    assert grade_counts[ConditionGrade.NORMAL] == 1
    assert grade_counts[ConditionGrade.MINT] == 0
    assert grade_counts[ConditionGrade.REJECT] == 0

    assert len(response.zone_stocks) == 2
    assert response.zone_stocks[0].zone == "A"
    assert response.zone_stocks[0].available_quantity == 7
    assert response.zone_stocks[1].zone == "B"
    assert response.zone_stocks[1].new_stock_quantity == 3
    assert response.zone_stocks[1].used_stock_quantity == 2
    assert response.zone_stocks[1].available_quantity == 5

    assert len(response.recent_activities) == 1
    assert response.recent_activities[0].book_title == (
        "입고 대시보드 테스트 도서"
    )
    assert response.recent_activities[0].location_barcode == "B-2-1"

    assert len(session.statements) == 8