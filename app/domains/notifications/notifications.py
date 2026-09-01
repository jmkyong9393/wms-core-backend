import json
from uuid import UUID

import redis.asyncio as redis
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlmodel import Session

from app.api.dependencies.auth import require_admin_or_master
from app.core.database import get_session
from app.core.config import settings
from app.models.wms import User
from app.domains.notifications.schemas.notification import (
    MarkAllNotificationsReadResponse,
    MarkNotificationReadResponse,
    NotificationListResponse,
    NotificationStreamTicketResponse,
)

from app.domains.notifications.notification_service import (
    get_notifications_for_user,
    mark_all_notifications_as_read,
    mark_notification_as_read,
)
from app.core.redis_pubsub import get_tenant_notification_channel
from app.core.sse_ticket_service import (
    issue_notification_sse_ticket,
    validate_notification_sse_ticket,
)


router = APIRouter()

@router.post(
    "/stream-ticket",
    response_model=NotificationStreamTicketResponse,
    summary="알림센터 SSE 연결 티켓 발급",
)
async def create_notification_stream_ticket(
    current_user: User = Depends(require_admin_or_master),
) -> NotificationStreamTicketResponse:
    ticket, expires_in = await issue_notification_sse_ticket(
        user_id=current_user.id,
        tenant_id=current_user.tenant_id,
    )

    return NotificationStreamTicketResponse(
        ticket=ticket,
        expires_in=expires_in,
    )


@router.get(
    "",
    response_model=NotificationListResponse,
    summary="내 알림 목록 조회",
)
def get_notifications(
    limit: int = Query(
        default=50,
        ge=1,
        le=100,
        description="조회할 최대 알림 수",
    ),
    current_user: User = Depends(require_admin_or_master),
    session: Session = Depends(get_session),
) -> NotificationListResponse:
    return get_notifications_for_user(
        session=session,
        current_user=current_user,
        limit=limit,
    )


@router.patch(
    "/read-all",
    response_model=MarkAllNotificationsReadResponse,
    summary="전체 알림 읽음 처리",
)
def mark_all_notifications_read(
    current_user: User = Depends(require_admin_or_master),
    session: Session = Depends(get_session),
) -> MarkAllNotificationsReadResponse:
    try:
        result = mark_all_notifications_as_read(
            session=session,
            current_user=current_user,
        )
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise


@router.patch(
    "/{notification_id}/read",
    response_model=MarkNotificationReadResponse,
    summary="개별 알림 읽음 처리",
)
def mark_notification_read(
    notification_id: UUID,
    current_user: User = Depends(require_admin_or_master),
    session: Session = Depends(get_session),
) -> MarkNotificationReadResponse:
    try:
        result = mark_notification_as_read(
            session=session,
            current_user=current_user,
            notification_id=notification_id,
        )
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise

@router.get(
    "/stream",
    summary="알림센터 SSE 스트림 연결",
)
async def stream_notifications(
    request: Request,
    ticket: str = Query(
        description="POST /stream-ticket으로 발급받은 단기 SSE 티켓",
    ),
) -> StreamingResponse:
    ticket_payload = await validate_notification_sse_ticket(ticket)

    if ticket_payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="유효하지 않거나 만료된 SSE 티켓입니다.",
        )

    tenant_id = ticket_payload["tenant_id"]
    channel = get_tenant_notification_channel(tenant_id)

    async def event_generator():
        redis_client = redis.Redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
        )
        pubsub = redis_client.pubsub()

        try:
            await pubsub.subscribe(channel)

            yield (
                "event: connected\n"
                f"data: {json.dumps({'tenant_id': tenant_id})}\n\n"
            )

            while not await request.is_disconnected():
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=settings.SSE_HEARTBEAT_SECONDS,
                )

                if message is None:
                    yield ": heartbeat\n\n"
                    continue

                if message["type"] != "message":
                    continue

                yield (
                    "event: notification\n"
                    f"data: {message['data']}\n\n"
                )

        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()
            await redis_client.aclose()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
