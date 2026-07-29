from types import SimpleNamespace
from uuid import UUID

from app.models.wms import ReturnJobStatus
from app import worker


def build_return_job(status: ReturnJobStatus):
    return SimpleNamespace(
        id=UUID("00000000-0000-4000-8000-000000000001"),
        status=status,
    )


def test_dispatches_restock_task_for_rejected_job(
    monkeypatch,
):
    captured = {}

    def fake_send_task(task_name, args, task_id):
        captured["task_name"] = task_name
        captured["args"] = args
        captured["task_id"] = task_id

    monkeypatch.setattr(
        worker.celery_app,
        "send_task",
        fake_send_task,
    )
    monkeypatch.setattr(
        worker,
        "uuid4",
        lambda: UUID(
            "00000000-0000-4000-8000-000000000010"
        ),
    )

    return_job = build_return_job(
        ReturnJobStatus.REJECTED,
    )

    worker.dispatch_restock_proposal_task_safely(
        return_job=return_job,
    )

    assert captured["task_name"] == (
        worker.RESTOCK_PROPOSAL_TASK_NAME
    )
    assert captured["args"] == [str(return_job.id)]
    assert captured["task_id"] == (
        "00000000-0000-4000-8000-000000000010"
    )


def test_does_not_dispatch_restock_task_for_approved_job(
    monkeypatch,
):
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError(
            "Restock task must not be dispatched"
        )

    monkeypatch.setattr(
        worker.celery_app,
        "send_task",
        fail_if_called,
    )

    return_job = build_return_job(
        ReturnJobStatus.APPROVED,
    )

    worker.dispatch_restock_proposal_task_safely(
        return_job=return_job,
    )


def test_does_not_raise_when_restock_task_dispatch_fails(
    monkeypatch,
):
    def raise_dispatch_error(*_args, **_kwargs):
        raise ConnectionError("Redis is unavailable")

    monkeypatch.setattr(
        worker.celery_app,
        "send_task",
        raise_dispatch_error,
    )

    return_job = build_return_job(
        ReturnJobStatus.REJECTED,
    )

    # 이미 완료된 WMS 반려 처리를 실패로 바꾸지 않아야 한다.
    worker.dispatch_restock_proposal_task_safely(
        return_job=return_job,
    )

class FakeSessionContext:
    def __init__(self, book=None):
        self.book = book

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def get(self, _model, _id):
        return self.book


def build_proposal(
    *,
    recommended_order_quantity: int,
    risk_level: str,
):
    return SimpleNamespace(
        id=UUID("00000000-0000-4000-8000-000000000020"),
        tenant_id=UUID(
            "00000000-0000-4000-8000-000000000021"
        ),
        book_id=UUID(
            "00000000-0000-4000-8000-000000000022"
        ),
        recommended_order_quantity=recommended_order_quantity,
        risk_level=risk_level,
    )


def test_zero_quantity_proposal_does_not_publish_notification(
    monkeypatch,
):
    proposal = build_proposal(
        recommended_order_quantity=0,
        risk_level="LOW",
    )

    monkeypatch.setattr(
        worker,
        "Session",
        lambda _engine: FakeSessionContext(),
    )
    monkeypatch.setattr(
        worker,
        "create_restock_proposal_for_rejected_job",
        lambda **_kwargs: SimpleNamespace(
            proposal=proposal,
            created=True,
        ),
    )

    def fail_if_notification_is_created(**_kwargs):
        raise AssertionError(
            "0권 추천안은 알림을 생성하면 안 됩니다."
        )

    monkeypatch.setattr(
        worker,
        "create_committed_notification_for_tenant",
        fail_if_notification_is_created,
    )

    task_result = worker.process_restock_proposal.apply(
        args=[
            "00000000-0000-4000-8000-000000000001",
        ],
    )

    assert task_result.successful()

    result = task_result.get()
    assert result["created"] is True
    assert result["notification_published"] is False


def test_positive_quantity_proposal_publishes_notification(
    monkeypatch,
):
    proposal = build_proposal(
        recommended_order_quantity=11,
        risk_level="HIGH",
    )
    book = SimpleNamespace(
        title="대체 발주 필요 도서",
    )
    published = {}

    monkeypatch.setattr(
        worker,
        "Session",
        lambda _engine: FakeSessionContext(book=book),
    )
    monkeypatch.setattr(
        worker,
        "create_restock_proposal_for_rejected_job",
        lambda **_kwargs: SimpleNamespace(
            proposal=proposal,
            created=True,
        ),
    )
    monkeypatch.setattr(
        worker,
        "create_committed_notification_for_tenant",
        lambda **_kwargs: (
            str(proposal.tenant_id),
            {"event": "notification"},
        ),
    )

    def fake_publish_event_safely(
        *,
        event_name,
        publish_function,
        **kwargs,
    ):
        published["event_name"] = event_name
        published["publish_function"] = publish_function
        published["kwargs"] = kwargs
        return True

    monkeypatch.setattr(
        worker,
        "publish_event_safely",
        fake_publish_event_safely,
    )

    task_result = worker.process_restock_proposal.apply(
        args=[
            "00000000-0000-4000-8000-000000000001",
        ],
    )

    assert task_result.successful()

    result = task_result.get()
    assert result["created"] is True
    assert result["notification_published"] is True
    assert published["event_name"] == "RESTOCK_ALERT"
    assert published["kwargs"]["tenant_id"] == str(
        proposal.tenant_id
    )