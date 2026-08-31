from datetime import date
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.models.wms import UserRole
from app.domains.auth.schemas.auth import EmployeeBulkCreateRow
from app.domains.auth import auth_service


class FakeSession:
    def __init__(self):
        self.added_users = []
        self.commit_count = 0
        self.rollback_count = 0

    def add(self, user):
        self.added_users.append(user)

    def commit(self):
        self.commit_count += 1

    def rollback(self):
        self.rollback_count += 1


def build_row(
    *,
    source_row: int,
    name: str,
    hire_date: date,
    role: UserRole = UserRole.WORKER,
    email: str | None = None,
) -> EmployeeBulkCreateRow:
    return EmployeeBulkCreateRow(
        source_row=source_row,
        name=name,
        hire_date=hire_date,
        role=role,
        email=email,
    )


def test_generate_employee_id_uses_monthly_three_digit_sequence(
    monkeypatch,
):
    class FakeResult:
        def all(self):
            return [
                "NZ2608001",
                "NZ2608002",
                # 과거 일 단위 사번은 순번 계산에서 제외
                "NZ26080101",
            ]

    class GenerateIdSession:
        def exec(self, _statement):
            return FakeResult()

    employee_id = auth_service.generate_employee_id(
        session=GenerateIdSession(),
        hire_date=date(2026, 8, 15),
    )

    assert employee_id == "NZ2608003"


def test_bulk_create_assigns_ids_by_hire_date_then_name(
    monkeypatch,
):
    session = FakeSession()
    current_master = SimpleNamespace(
        tenant_id=UUID(
            "00000000-0000-4000-8000-000000000100"
        )
    )

    monkeypatch.setattr(
        auth_service,
        "_lock_employee_id_month",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        auth_service,
        "_get_issued_employee_sequences",
        lambda **_kwargs: {10},
    )
    monkeypatch.setattr(
        auth_service,
        "generate_temporary_password",
        lambda: "TempPassword123!",
    )
    monkeypatch.setattr(
        auth_service,
        "hash_password",
        lambda _password: "hashed-password",
    )

    results = auth_service.create_employees_bulk(
        session=session,
        current_master=current_master,
        rows=[
            # 원본 엑셀 순서는 아래 순서지만,
            # 사번은 입사일 → 이름 순으로 배정돼야 한다.
            build_row(
                source_row=2,
                name="다라",
                hire_date=date(2026, 8, 2),
            ),
            build_row(
                source_row=3,
                name="나다",
                hire_date=date(2026, 8, 1),
            ),
            build_row(
                source_row=4,
                name="가나",
                hire_date=date(2026, 8, 1),
            ),
        ],
    )

    # 결과 행 자체는 원본 엑셀 순서를 유지한다.
    assert [
        result.source_row
        for result in results
    ] == [2, 3, 4]

    # 사번은 입사일 → 이름 순서로 부여된다.
    assert [
        result.employee_id
        for result in results
    ] == [
        "NZ2608013",
        "NZ2608012",
        "NZ2608011",
    ]

    assert session.commit_count == 1
    assert session.rollback_count == 0
    assert len(session.added_users) == 3

    assert all(
        user.must_change_password is True
        for user in session.added_users
    )
    assert all(
        user.password_hash == "hashed-password"
        for user in session.added_users
    )


def test_bulk_create_rejects_duplicate_email_before_commit():
    session = FakeSession()
    current_master = SimpleNamespace(
        tenant_id=UUID(
            "00000000-0000-4000-8000-000000000100"
        )
    )

    with pytest.raises(
        ValueError,
        match="이메일이 중복",
    ):
        auth_service.create_employees_bulk(
            session=session,
            current_master=current_master,
            rows=[
                build_row(
                    source_row=2,
                    name="가나",
                    hire_date=date(2026, 8, 1),
                    email="same@example.com",
                ),
                build_row(
                    source_row=3,
                    name="나다",
                    hire_date=date(2026, 8, 1),
                    email="same@example.com",
                ),
            ],
        )

    assert session.commit_count == 0
    assert session.added_users == []