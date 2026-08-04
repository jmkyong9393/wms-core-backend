from types import SimpleNamespace
from uuid import UUID

import pytest

from app.ai.demand import auto_po_batch


class FakeSessionContext:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_batch_queues_safety_stock_restock_tasks(
    monkeypatch,
):
    tenant_id = UUID(
        "00000000-0000-4000-8000-000000000100"
    )
    queued_tasks = []

    monkeypatch.setattr(
        auto_po_batch,
        "Session",
        lambda _engine: FakeSessionContext(),
    )
    monkeypatch.setattr(
        auto_po_batch.settings,
        "AUTO_PO_TENANT_ID",
        tenant_id,
    )
    monkeypatch.setattr(
        auto_po_batch,
        "fetch_books_needing_restock",
        lambda _session: [
            {
                "book_id": (
                    "00000000-0000-4000-8000-000000000001"
                ),
                "title": "안전재고 부족 도서",
                "weekly_sales": 8,
                "current_stock": 2,
                "pending_auto_po_quantity": 0,
                "safety_stock_quantity": 16,
            },
        ],
    )

    def fake_send_task(task_name, args):
        queued_tasks.append(
            {
                "task_name": task_name,
                "args": args,
            }
        )
        return SimpleNamespace(id="batch-task-001")

    monkeypatch.setattr(
        auto_po_batch.celery_app,
        "send_task",
        fake_send_task,
    )

    auto_po_batch.run_auto_po_batch()

    assert queued_tasks == [
        {
            "task_name": (
                "app.worker."
                "process_safety_stock_restock_proposal"
            ),
            "args": [
                str(tenant_id),
                "00000000-0000-4000-8000-000000000001",
                16,
            ],
        },
    ]


def test_batch_does_not_queue_task_when_no_book_is_short(
    monkeypatch,
):
    monkeypatch.setattr(
        auto_po_batch,
        "Session",
        lambda _engine: FakeSessionContext(),
    )
    monkeypatch.setattr(
        auto_po_batch.settings,
        "AUTO_PO_TENANT_ID",
        UUID("00000000-0000-4000-8000-000000000100"),
    )
    monkeypatch.setattr(
        auto_po_batch,
        "fetch_books_needing_restock",
        lambda _session: [],
    )

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError(
            "안전재고 부족 도서가 없으면 Task를 등록하면 안 됩니다."
        )

    monkeypatch.setattr(
        auto_po_batch.celery_app,
        "send_task",
        fail_if_called,
    )

    auto_po_batch.run_auto_po_batch()