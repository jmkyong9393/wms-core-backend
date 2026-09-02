from datetime import datetime
from uuid import UUID
from types import SimpleNamespace
import pytest
from fastapi import HTTPException

from app.models.wms import User, UserRole, UserStatus, FdsPolicy
from app.domains.admin.admin import (
    get_weekly_insights,
    get_fds_reports,
    get_fds_policies,
    update_fds_policy,
)
from app.domains.admin.schemas.admin_dashboard import FdsPolicyUpdateRequest


class FakeResult:
    def __init__(self, rows=None):
        self.rows = rows or []

    def all(self):
        return self.rows


class FakeSession:
    def __init__(self, results=None):
        self.results = results or []
        self.statements = []
        self.added = []
        self.committed = False
        self.refreshed = False

    def exec(self, statement):
        self.statements.append(statement)
        if self.results:
            return self.results.pop(0)
        return FakeResult()

    def add(self, item):
        self.added.append(item)

    def commit(self):
        self.committed = True

    def refresh(self, item):
        self.refreshed = True

    def get(self, model, key):
        # For put/update test
        if model == FdsPolicy:
            return FdsPolicy(policy_key=key, policy_value=3.0, description="Test description")
        return None


def test_get_weekly_insights():
    mock_insight = SimpleNamespace(
        id=UUID("00000000-0000-4000-8000-000000000001"),
        report_week="2026-W28",
        saved_labor_cost_krw=1200000,
        top_defective_publishers={"PublisherA": 5},
        location_hotspots={"A-1-1": 2},
        logistics_hotspots={"SEOUL_DC": 1},
        predicted_returns=15,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )

    session = FakeSession(results=[FakeResult(rows=[mock_insight])])
    admin = User(
        id=UUID("00000000-0000-4000-8000-000000000100"),
        tenant_id=UUID("00000000-0000-4000-8000-000000000200"),
        employee_id="Admin01",
        name="Admin",
        password_hash="...",
        role=UserRole.ADMIN,
    )

    response = get_weekly_insights(current_admin=admin, session=session)
    assert len(response) == 1
    assert response[0].report_week == "2026-W28"
    assert response[0].saved_labor_cost_krw == 1200000


def test_get_fds_reports():
    mock_report = SimpleNamespace(
        id=UUID("00000000-0000-4000-8000-000000000002"),
        tenant_id=UUID("00000000-0000-4000-8000-000000000200"),
        customer_id=UUID("00000000-0000-4000-8000-000000000003"),
        fraud_score=95,
        fraud_reason="상습 반품",
        detected_at=datetime.utcnow(),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )

    # First result is FdsReport select. Second result is Order select for customer name.
    session = FakeSession(
        results=[
            FakeResult(rows=[mock_report]),
            FakeResult(rows=[(UUID("00000000-0000-4000-8000-000000000003"), "B2B 고객사A")]),
        ]
    )

    admin = User(
        id=UUID("00000000-0000-4000-8000-000000000100"),
        tenant_id=UUID("00000000-0000-4000-8000-000000000200"),
        employee_id="Admin01",
        name="Admin",
        password_hash="...",
        role=UserRole.ADMIN,
    )

    response = get_fds_reports(current_admin=admin, session=session)
    assert len(response) == 1
    assert response[0].fraud_score == 95
    assert response[0].customer_name == "B2B 고객사A"


def test_get_fds_policies_seeds_when_empty():
    session = FakeSession(
        results=[
            FakeResult(rows=[]),  # No existing policies triggers seeding
            FakeResult(
                rows=[
                    SimpleNamespace(
                        policy_key="MAX_RETURN_30D", policy_value=3.0, description="Desc", updated_at=datetime.utcnow()
                    )
                ]
            ),
        ]
    )

    admin = User(
        id=UUID("00000000-0000-4000-8000-000000000100"),
        tenant_id=UUID("00000000-0000-4000-8000-000000000200"),
        employee_id="Admin01",
        name="Admin",
        password_hash="...",
        role=UserRole.ADMIN,
    )

    response = get_fds_policies(current_admin=admin, session=session)
    # Default policy seeding adds 4 policies
    assert len(session.added) == 4
    assert session.committed is True
    # The actual select query returns our mocked select
    assert len(response) == 1
    assert response[0].policy_key == "MAX_RETURN_30D"


def test_update_fds_policy():
    session = FakeSession(results=[FakeResult(rows=[SimpleNamespace(policy_key="MAX_RETURN_30D")])])

    admin = User(
        id=UUID("00000000-0000-4000-8000-000000000100"),
        tenant_id=UUID("00000000-0000-4000-8000-000000000200"),
        employee_id="Admin01",
        name="Admin",
        password_hash="...",
        role=UserRole.ADMIN,
    )

    req = FdsPolicyUpdateRequest(policy_value=10.0)
    response = update_fds_policy(policy_key="MAX_RETURN_30D", request=req, current_admin=admin, session=session)

    assert response.policy_value == 10.0
    assert session.committed is True
