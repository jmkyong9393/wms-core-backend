import json
import secrets
from typing import Any
from uuid import UUID

import redis.asyncio as redis

from app.core.config import settings

SSE_TICKET_KEY_PREFIX = "sse_ticket"
NOTIFICATION_SSE_TICKET_KEY_PREFIX = "notification_sse_ticket"


def get_sse_ticket_key(ticket: str) -> str:
    return f"{SSE_TICKET_KEY_PREFIX}:{ticket}"


# JWT 인증을 통과한 사용자에게 TTL 동안 재접속 가능한 단기 SSE 티켓 발급
async def issue_sse_ticket(
    job_id: UUID,
    user_id: UUID,
    tenant_id: UUID,
) -> tuple[str, int]:
    ticket = secrets.token_urlsafe(32)
    expires_in = settings.SSE_TICKET_EXPIRE_SECONDS

    redis_client = redis.Redis.from_url(
        settings.REDIS_URL,
        decode_responses=True,
    )

    payload: dict[str, Any] = {
        "job_id": str(job_id),
        "user_id": str(user_id),
        "tenant_id": str(tenant_id),
    }

    try:
        await redis_client.set(
            get_sse_ticket_key(ticket),
            json.dumps(payload),
            ex=expires_in,
        )
    finally:
        await redis_client.aclose()

    return ticket, expires_in


# 티켓을 삭제하지 않고 유효성만 확인
# TTL이 유지되는 동안 동일 티켓으로 SSE 재접속 가능
async def validate_sse_ticket(
    ticket: str,
    job_id: UUID,
) -> dict[str, Any] | None:
    redis_client = redis.Redis.from_url(
        settings.REDIS_URL,
        decode_responses=True,
    )

    try:
        raw_payload = await redis_client.get(get_sse_ticket_key(ticket))
    finally:
        await redis_client.aclose()

    if raw_payload is None:
        return None

    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError:
        return None

    # 티켓이 발급된 검수 작업과 요청 경로의 job_id가 같은지 확인
    if payload.get("job_id") != str(job_id):
        return None

    if payload.get("user_id") is None:
        return None

    if payload.get("tenant_id") is None:
        return None

    return payload


def get_notification_sse_ticket_key(
    ticket: str,
) -> str:
    """알림센터 SSE 티켓의 Redis 키를 만든다."""
    return f"{NOTIFICATION_SSE_TICKET_KEY_PREFIX}:{ticket}"


async def issue_notification_sse_ticket(
    user_id: UUID,
    tenant_id: UUID,
) -> tuple[str, int]:
    """관리자 알림센터용 단기 SSE 티켓을 발급한다."""
    ticket = secrets.token_urlsafe(32)
    expires_in = settings.SSE_TICKET_EXPIRE_SECONDS

    redis_client = redis.Redis.from_url(
        settings.REDIS_URL,
        decode_responses=True,
    )

    payload: dict[str, Any] = {
        "user_id": str(user_id),
        "tenant_id": str(tenant_id),
    }

    try:
        await redis_client.set(
            get_notification_sse_ticket_key(ticket),
            json.dumps(payload),
            ex=expires_in,
        )
    finally:
        await redis_client.aclose()

    return ticket, expires_in


async def validate_notification_sse_ticket(
    ticket: str,
) -> dict[str, Any] | None:
    """알림센터 SSE 티켓을 검증하고 사용자·테넌트 정보를 반환한다."""
    redis_client = redis.Redis.from_url(
        settings.REDIS_URL,
        decode_responses=True,
    )

    try:
        raw_payload = await redis_client.get(get_notification_sse_ticket_key(ticket))
    finally:
        await redis_client.aclose()

    if raw_payload is None:
        return None

    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError:
        return None

    if payload.get("user_id") is None:
        return None

    if payload.get("tenant_id") is None:
        return None

    return payload
