from uuid import UUID

from app.ai.reporting.report_batch import save_fds_report


class FakeResult:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class FakeSession:
    def __init__(self, rows):
        self.rows = rows
        self.execute_calls = []
        self.added_items = []

    def execute(self, statement, params=None):
        self.execute_calls.append(
            {
                "statement": str(statement),
                "params": params,
            }
        )
        row = self.rows.pop(0) if self.rows else None
        return FakeResult(row)

    def add(self, item):
        self.added_items.append(item)


def build_record(score=95):
    return {
        "tenant_id": "00000000-0000-4000-8000-000000000010",
        "customer_id": "00000000-0000-4000-8000-000000000020",
        "customer_name": "홍길동",
        "fraud_score": score,
        "fraud_reason": "상습 고의 파손 의심",
        "returns_last_30d": 4,
        "returns_last_90d": 6,
        "total_refund_amount": 600000.0,
    }


def test_creates_new_tenant_scoped_fds_report():
    session = FakeSession(rows=[None])
    record = build_record()

    candidates = save_fds_report(session, [record])

    assert candidates == [record]
    assert len(session.added_items) == 1

    report = session.added_items[0]
    assert report.tenant_id == UUID(record["tenant_id"])
    assert report.customer_id == UUID(record["customer_id"])
    assert report.fraud_score == 95

    select_params = session.execute_calls[0]["params"]
    assert select_params["tenant_id"] == UUID(record["tenant_id"])
    assert select_params["cid"] == UUID(record["customer_id"])


def test_updates_existing_report_when_score_increases():
    report_id = UUID("00000000-0000-4000-8000-000000000030")
    session = FakeSession(rows=[(report_id, 75), None])
    record = build_record(95)

    candidates = save_fds_report(session, [record])

    assert candidates == [record]
    assert session.added_items == []

    update_params = session.execute_calls[1]["params"]
    assert update_params["id"] == report_id
    assert update_params["score"] == 95


def test_keeps_existing_report_when_score_does_not_increase():
    report_id = UUID("00000000-0000-4000-8000-000000000030")
    session = FakeSession(rows=[(report_id, 95)])
    record = build_record(75)

    candidates = save_fds_report(session, [record])

    assert candidates == []
    assert session.added_items == []
    assert len(session.execute_calls) == 1
