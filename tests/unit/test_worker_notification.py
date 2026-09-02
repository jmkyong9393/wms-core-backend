from types import SimpleNamespace
from uuid import UUID

from app.worker import tasks as worker
from app.models.wms import (
    NotificationCategory,
    NotificationSeverity,
)


def test_hitl_agent_alert_is_saved_then_published(
    monkeypatch,
):
    captured = {}

    def fake_create_notification(**kwargs):
        captured["create"] = kwargs

        return (
            "00000000-0000-4000-8000-000000000010",
            {
                "id": "00000000-0000-4000-8000-000000000020",
                "category": "AGENT_ALERT",
                "severity": "MEDIUM",
                "title": "AI 검수 결과 관리자 확인 필요",
                "message": ("AI 검수가 자동 확정되지 않아 관리자 검수가 필요합니다. 사유 코드: VISION_LOW_CONFIDENCE"),
                "timestamp": "2026-07-28T12:00:00",
                "read": False,
            },
        )

    def fake_publish_event_safely(**kwargs):
        captured["publish"] = kwargs
        return True

    monkeypatch.setattr(
        worker,
        "create_committed_notification_for_tenant",
        fake_create_notification,
    )
    monkeypatch.setattr(
        worker,
        "publish_event_safely",
        fake_publish_event_safely,
    )

    job = SimpleNamespace(
        id=UUID("00000000-0000-4000-8000-000000000001"),
        tenant_id=UUID("00000000-0000-4000-8000-000000000010"),
    )

    worker.create_agent_hitl_alert_safely(
        job=job,
        ai_result={
            "reason_code": "VISION_LOW_CONFIDENCE",
        },
        task_id="inspection-task-001",
    )

    assert captured["create"]["tenant_id"] == job.tenant_id
    assert captured["create"]["category"] == NotificationCategory.AGENT_ALERT
    assert captured["create"]["severity"] == NotificationSeverity.MEDIUM
    assert captured["create"]["payload"] == {
        "return_job_id": str(job.id),
        "inspection_task_id": "inspection-task-001",
        "reason_code": "VISION_LOW_CONFIDENCE",
    }

    assert captured["publish"]["event_name"] == "AGENT_ALERT"
    assert captured["publish"]["publish_function"] is worker.publish_tenant_notification_event
    assert captured["publish"]["tenant_id"] == str(job.tenant_id)
    assert captured["publish"]["event"]["category"] == "AGENT_ALERT"
    assert captured["publish"]["event"]["read"] is False


def test_hitl_agent_alert_does_not_publish_when_db_save_fails(
    monkeypatch,
):
    published = False

    def fake_create_notification(**kwargs):
        raise RuntimeError("notification database error")

    def fake_publish_event_safely(**kwargs):
        nonlocal published
        published = True
        return True

    monkeypatch.setattr(
        worker,
        "create_committed_notification_for_tenant",
        fake_create_notification,
    )
    monkeypatch.setattr(
        worker,
        "publish_event_safely",
        fake_publish_event_safely,
    )

    job = SimpleNamespace(
        id=UUID("00000000-0000-4000-8000-000000000001"),
        tenant_id=UUID("00000000-0000-4000-8000-000000000010"),
    )

    worker.create_agent_hitl_alert_safely(
        job=job,
        ai_result={},
        task_id="inspection-task-001",
    )

    assert published is False
