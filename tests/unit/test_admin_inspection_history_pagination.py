from datetime import datetime
from types import SimpleNamespace
from uuid import UUID

from app.models.wms import (
    ConditionGrade,
    ReturnJobStatus,
)
from app.services.admin_inspection_service import (
    get_inspection_history,
)


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


def build_return_job(
    job_id: str,
    created_at: datetime,
):
    return SimpleNamespace(
        id=UUID(job_id),
        book_id=UUID(
            "00000000-0000-4000-8000-000000000010"
        ),
        status=ReturnJobStatus.REJECTED,
        ubci_score=72,
        final_report="검수 반려",
        agent_logs={
            "final_grade": "REJECT",
            "reason_code": "DMG_EXT_WET",
            "is_fast_track": False,
            "steps": [],
        },
        condition_grade=ConditionGrade.EXCELLENT,
        created_at=created_at,
        updated_at=created_at,
    )


def test_returns_requested_inspection_history_page():
    return_job = build_return_job(
        job_id="00000000-0000-4000-8000-000000000001",
        created_at=datetime(2026, 7, 30, 10, 0, 0),
    )

    session = FakeSession(
        results=[
            FakeResult(one_value=3),
            FakeResult(
                rows=[
                    (
                        return_job,
                        "페이지네이션 테스트 도서",
                        "https://example.com/book-cover.jpg",
                    )
                ]
            ),
        ]
    )

    response = get_inspection_history(
        session=session,
        tenant_id=UUID(
            "00000000-0000-4000-8000-000000000100"
        ),
        status=ReturnJobStatus.REJECTED,
        keyword="테스트",
        page=2,
        size=1,
    )

    assert response.total == 3
    assert response.page == 2
    assert response.size == 1
    assert response.total_pages == 3
    assert len(response.items) == 1
    assert response.items[0].book_title == (
        "페이지네이션 테스트 도서"
    )
    assert response.items[0].status == ReturnJobStatus.REJECTED

    history_statement = session.statements[1]

    assert history_statement._offset_clause.value == 1
    assert history_statement._limit_clause.value == 1

    assert response.items[0].cover_image_url == (
        "https://example.com/book-cover.jpg"
    )


def test_returns_zero_total_pages_when_history_is_empty():
    session = FakeSession(
        results=[
            FakeResult(one_value=0),
            FakeResult(rows=[]),
        ]
    )

    response = get_inspection_history(
        session=session,
        tenant_id=UUID(
            "00000000-0000-4000-8000-000000000100"
        ),
        page=1,
        size=20,
    )

    assert response.items == []
    assert response.total == 0
    assert response.page == 1
    assert response.size == 20
    assert response.total_pages == 0


def test_applies_grade_fast_track_and_reason_filters_to_queries():
    return_job = build_return_job(
        job_id="00000000-0000-4000-8000-000000000001",
        created_at=datetime(2026, 7, 30, 10, 0, 0),
    )
    return_job.agent_logs["is_fast_track"] = True

    session = FakeSession(
        results=[
            FakeResult(one_value=1),
            FakeResult(
                rows=[
                    (
                        return_job,
                        "필터 테스트 도서",
                        None,
                    )
                ]
            ),
        ]
    )

    response = get_inspection_history(
        session=session,
        tenant_id=UUID(
            "00000000-0000-4000-8000-000000000100"
        ),
        grade=ConditionGrade.EXCELLENT,
        fast_track=True,
        reason_code="DMG_EXT_WET",
        page=1,
        size=20,
    )

    assert response.total == 1
    assert response.items[0].final_grade == "EXCELLENT"

    for statement in session.statements:
        parameters = statement.compile().params.values()
        statement_sql = str(statement).lower()

        assert any(
            getattr(value, "value", value) == "EXCELLENT"
            for value in parameters
        )
        assert "is_fast_track" in parameters
        assert "true" in statement_sql
        assert "DMG_EXT_WET" in parameters

def test_treats_missing_fast_track_log_as_normal_inspection():
    session = FakeSession(
        results=[
            FakeResult(one_value=0),
            FakeResult(rows=[]),
        ]
    )

    get_inspection_history(
        session=session,
        tenant_id=UUID(
            "00000000-0000-4000-8000-000000000100"
        ),
        fast_track=False,
        page=1,
        size=20,
    )

    for statement in session.statements:
        statement_sql = str(statement).lower()

        assert "coalesce" in statement_sql
        assert "is_fast_track" in statement.compile().params.values()