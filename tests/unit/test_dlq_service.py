import json

from app.services import dlq_service


class FakePipeline:
    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def rpush(self, *args):
        self.calls.append(("rpush", args))
        return self

    def ltrim(self, *args):
        self.calls.append(("ltrim", args))
        return self

    def expire(self, *args):
        self.calls.append(("expire", args))
        return self

    def execute(self):
        self.calls.append(("execute", ()))


class FakeRedis:
    def __init__(self):
        self.pipeline_instance = FakePipeline()
        self.closed = False

    def pipeline(self, transaction):
        assert transaction is True
        return self.pipeline_instance

    def close(self):
        self.closed = True


def test_dlq_limits_entries_and_sets_ttl(monkeypatch):
    fake_redis = FakeRedis()

    monkeypatch.setattr(
        dlq_service.redis.Redis,
        "from_url",
        lambda *args, **kwargs: fake_redis,
    )
    monkeypatch.setattr(
        dlq_service.settings,
        "INSPECTION_DLQ_MAX_ENTRIES",
        1000,
    )
    monkeypatch.setattr(
        dlq_service.settings,
        "INSPECTION_DLQ_TTL_SECONDS",
        60 * 60 * 24 * 14,
    )

    dlq_service.push_inspection_failure_to_dlq(
        job_id="job-1",
        task_id="task-1",
        source_task="app.worker.process_wms_action",
        error=RuntimeError("WMS unavailable"),
        retry_count=3,
    )

    calls = fake_redis.pipeline_instance.calls

    assert calls[0][0] == "rpush"
    assert calls[0][1][0] == dlq_service.INSPECTION_DLQ_KEY

    message = json.loads(calls[0][1][1])

    assert message["job_id"] == "job-1"
    assert message["task_id"] == "task-1"
    assert message["error_type"] == "RuntimeError"
    assert message["error_message"] == "WMS unavailable"
    assert message["retry_count"] == 3

    assert calls[1] == (
        "ltrim",
        (
            dlq_service.INSPECTION_DLQ_KEY,
            -1000,
            -1,
        ),
    )

    assert calls[2] == (
        "expire",
        (
            dlq_service.INSPECTION_DLQ_KEY,
            60 * 60 * 24 * 14,
        ),
    )

    assert calls[3] == ("execute", ())
    assert fake_redis.closed is True