import json
from datetime import UTC, datetime
from typing import Any

import redis

from app.core.config import settings

INSPECTION_DLQ_KEY = "wms:dlq:inspection"


# 실패한 Celery 작업 정보를 Redis DLQ에 저장
def push_inspection_failure_to_dlq(
    job_id: str,
    task_id: str,
    source_task: str,
    error: Exception,
    retry_count: int,
) -> None:
    redis_client = redis.Redis.from_url(
        settings.REDIS_URL,
        decode_responses=True,
    )

    dlq_message: dict[str, Any] = {
        "job_id": job_id,
        "task_id": task_id,
        "source_task": source_task,
        "error_type": type(error).__name__,
        "error_message": str(error),
        "retry_count": retry_count,
        "failed_at": datetime.now(UTC).isoformat(),
    }

    try:
        with redis_client.pipeline(transaction=True) as pipeline:
            pipeline.rpush(
                INSPECTION_DLQ_KEY,
                json.dumps(
                    dlq_message,
                    ensure_ascii=False,
                ),
            )
            pipeline.ltrim(
                INSPECTION_DLQ_KEY,
                -settings.INSPECTION_DLQ_MAX_ENTRIES,
                -1,
            )
            pipeline.expire(
                INSPECTION_DLQ_KEY,
                settings.INSPECTION_DLQ_TTL_SECONDS,
            )
            pipeline.execute()
    finally:
        redis_client.close()
