import json
import os
from datetime import datetime, timezone
from typing import Any

import redis


REDIS_URL = os.getenv(
    "REDIS_URL",
    "redis://localhost:6379/0",
)

INSPECTION_DLQ_KEY = "wms:dlq:inspection"


def push_inspection_failure_to_dlq(
    job_id: str,
    task_id: str,
    error: Exception,
    retry_count: int,
) -> None:
    redis_client = redis.Redis.from_url(
        REDIS_URL,
        decode_responses=True,
    )

    dlq_message: dict[str, Any] = {
        "job_id": job_id,
        "task_id": task_id,
        "source_task": "app.worker.process_inspection",
        "error_type": type(error).__name__,
        "error_message": str(error),
        "retry_count": retry_count,
        "failed_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        redis_client.rpush(
            INSPECTION_DLQ_KEY,
            json.dumps(
                dlq_message,
                ensure_ascii=False,
            ),
        )
    finally:
        redis_client.close()