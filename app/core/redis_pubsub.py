import json
import os
from typing import Any

import redis

REDIS_URL = os.getenv(
    "REDIS_URL",
    "redis://localhost:6379/0",
)

NOTIFICATION_TENANT_CHANNEL_PREFIX = "notification:tenant"


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


def get_tenant_notification_channel(
    tenant_id: str,
) -> str:
    """테넌트별 알림 SSE용 Redis Pub/Sub 채널명을 만든다."""
    return f"{NOTIFICATION_TENANT_CHANNEL_PREFIX}:{tenant_id}"


def publish_tenant_notification_event(
    tenant_id: str,
    event: dict[str, Any],
) -> None:
    """테넌트 관리자에게 전달할 알림 이벤트를 Redis Pub/Sub으로 발행한다."""
    redis_client = redis.Redis.from_url(
        REDIS_URL,
        decode_responses=True,
    )

    try:
        redis_client.publish(
            get_tenant_notification_channel(tenant_id),
            json.dumps(
                event,
                ensure_ascii=False,
            ),
        )
    finally:
        redis_client.close()
