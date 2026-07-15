import json
import os
from typing import Any

import redis

REDIS_URL = os.getenv(
    "REDIS_URL",
    "redis://localhost:6379/0",
)

# ReturnJob별 Redis Pub/Sub 채널명 생성
def get_return_job_channel(
    job_id: str,
) -> str:
    return f"return_job:{job_id}"


# Redis Pub/Sub 채널에 검수 상태 이벤트 발행
def publish_return_job_event(
    job_id: str,
    event: dict[str, Any],
) -> None:
    redis_client = redis.Redis.from_url(
        REDIS_URL,
        decode_responses=True,
    )

    try:
        redis_client.publish(
            get_return_job_channel(job_id),
            json.dumps(
                event,
                ensure_ascii=False,
            ),
        )

    finally:
        redis_client.close()