from datetime import datetime
from types import SimpleNamespace
from uuid import UUID

from app.models.wms import UserRole, UserStatus
from app.domains.auth.auth_service import list_employees


class FakeResult:
    def __init__(
        self,
        one_value=None,
        rows=None,
    ):
        self.one_value = one_value
        self.rows = rows or []

    def one(self):
        return self.one_value

    def all(self):
        return self.rows


class FakeSession:
    def __init__(self, results):
        self.results = results
        self.statements = []

    def exec(self, statement):
        self.statements.append(statement)
        return self.results.pop(0)


def build_user() -> SimpleNamespace:
    return SimpleNamespace(
        id=UUID("00000000-0000-4000-8000-000000000001"),
        employee_id="NZ26080201",
        email="hong@example.com",
        name="홍길동",
        role=UserRole.WORKER,
        status=UserStatus.ACTIVE,
        must_change_password=True,
        created_at=datetime(2026, 8, 2, 9, 0, 0),
    )


def test_returns_paginated_employee_list_with_user_uuid():
    session = FakeSession(
        results=[
            FakeResult(one_value=3),
            FakeResult(rows=[build_user()]),
        ]
    )

    response = list_employees(
        session=session,
        tenant_id=UUID(
            "00000000-0000-4000-8000-000000000100"
        ),
        keyword="홍",
        role=UserRole.WORKER,
        status=UserStatus.ACTIVE,
        page=2,
        size=1,
    )

    assert response.total == 3
    assert response.page == 2
    assert response.size == 1
    assert response.total_pages == 3

    assert len(response.items) == 1

    employee = response.items[0]

    assert str(employee.id) == (
        "00000000-0000-4000-8000-000000000001"
    )
    assert employee.employee_id == "NZ26080201"
    assert employee.name == "홍길동"
    assert employee.role == UserRole.WORKER
    assert employee.status == UserStatus.ACTIVE
    assert employee.must_change_password is True

    # 민감한 인증 정보는 목록 응답에 포함하지 않는다.
    assert not hasattr(employee, "password_hash")
    assert not hasattr(employee, "refresh_token_hash")

    list_statement = session.statements[1]

    assert list_statement._offset_clause.value == 1
    assert list_statement._limit_clause.value == 1


def test_applies_employee_filters_to_count_and_list_queries():
    tenant_id = UUID(
        "00000000-0000-4000-8000-000000000100"
    )
    session = FakeSession(
        results=[
            FakeResult(one_value=0),
            FakeResult(rows=[]),
        ]
    )

    list_employees(
        session=session,
        tenant_id=tenant_id,
        keyword="홍",
        role=UserRole.WORKER,
        status=UserStatus.ACTIVE,
        page=1,
        size=20,
    )

    for statement in session.statements:
        parameters = statement.compile().params.values()
        statement_sql = str(statement).lower()

        assert tenant_id in parameters
        assert "%홍%" in parameters
        assert any(
            getattr(value, "value", value) == "WORKER"
            for value in parameters
        )
        assert any(
            getattr(value, "value", value) == "ACTIVE"
            for value in parameters
        )

        assert "users.tenant_id" in statement_sql
        assert "users.employee_id" in statement_sql
        assert "users.name" in statement_sql
        assert "users.email" in statement_sql


def test_returns_zero_total_pages_for_empty_employee_list():
    session = FakeSession(
        results=[
            FakeResult(one_value=0),
            FakeResult(rows=[]),
        ]
    )

    response = list_employees(
        session=session,
        tenant_id=UUID(
            "00000000-0000-4000-8000-000000000100"
        ),
        page=1,
        size=20,
    )

    assert response.items == []
    assert response.total == 0
    assert response.total_pages == 0