from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, update
from sqlmodel import Session, col, select

from app.core.database import engine
from app.domains.notifications.schemas.notification import (
    MarkAllNotificationsReadResponse,
    MarkNotificationReadResponse,
    NotificationListResponse,
    NotificationResponse,
)
from app.models.wms import (
    Notification,
    NotificationCategory,
    NotificationRecipient,
    NotificationSeverity,
    User,
    UserRole,
    UserStatus,
)

DEFAULT_NOTIFICATION_LIMIT = 50


def create_committed_notification_for_tenant(
    *,
    tenant_id: UUID,
    category: NotificationCategory,
    severity: NotificationSeverity,
    title: str,
    message: str,
    payload: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """
    Worker처럼 요청 DB 세션이 없는 곳에서 알림을 별도 트랜잭션으로 저장한다.

    반환된 이벤트는 호출자가 DB 커밋 이후 Redis Pub/Sub으로 발행한다.
    """
    with Session(engine) as session:
        try:
            notification = create_notification_for_tenant(
                session=session,
                tenant_id=tenant_id,
                category=category,
                severity=severity,
                title=title,
                message=message,
                payload=payload,
            )

            event = {
                "id": str(notification.id),
                "category": notification.category.value,
                "severity": notification.severity.value,
                "title": notification.title,
                "message": notification.message,
                "payload": notification.payload,
                "timestamp": notification.created_at.isoformat(),
                "read": False,
            }

            session.commit()
            return str(notification.tenant_id), event

        except Exception:
            session.rollback()
            raise


def build_notification_response(
    notification: Notification,
    recipient: NotificationRecipient,
) -> NotificationResponse:
    """DB 알림과 수신자 읽음 상태를 프론트 공통 알림 형식으로 변환한다."""
    return NotificationResponse(
        id=notification.id,
        category=notification.category,
        severity=notification.severity,
        title=notification.title,
        message=notification.message,
        payload=notification.payload,
        timestamp=notification.created_at,
        read=recipient.read_at is not None,
    )


def create_notification_for_tenant(
    *,
    session: Session,
    tenant_id: UUID,
    category: NotificationCategory,
    severity: NotificationSeverity,
    title: str,
    message: str,
    payload: dict[str, Any] | None = None,
) -> Notification:
    """
    테넌트의 활성 ADMIN·MASTER에게 전달할 알림과 수신자 정보를 생성한다.

    호출한 쪽에서 session.commit() 성공 후 SSE 이벤트를 발행해야 한다.
    """
    notification = Notification(
        tenant_id=tenant_id,
        category=category,
        severity=severity,
        title=title,
        message=message,
        payload=payload or {},
    )
    session.add(notification)
    session.flush()

    recipients = session.exec(
        select(User).where(
            User.tenant_id == tenant_id,
            User.status == UserStatus.ACTIVE,
            col(User.role).in_(
                [
                    UserRole.MASTER,
                    UserRole.ADMIN,
                ]
            ),
        )
    ).all()

    for user in recipients:
        session.add(
            NotificationRecipient(
                notification_id=notification.id,
                user_id=user.id,
            )
        )

    session.flush()
    return notification


def get_notifications_for_user(
    *,
    session: Session,
    current_user: User,
    limit: int = DEFAULT_NOTIFICATION_LIMIT,
) -> NotificationListResponse:
    """현재 로그인 사용자가 수신한 최근 알림과 읽지 않은 개수를 조회한다."""
    rows = session.exec(
        select(Notification, NotificationRecipient)
        .join(
            NotificationRecipient,
            Notification.id == NotificationRecipient.notification_id,
        )
        .where(
            NotificationRecipient.user_id == current_user.id,
            Notification.tenant_id == current_user.tenant_id,
        )
        .order_by(col(Notification.created_at).desc())
        .limit(limit)
    ).all()

    unread_count = session.exec(
        select(func.count())
        .select_from(NotificationRecipient)
        .join(
            Notification,
            Notification.id == NotificationRecipient.notification_id,
        )
        .where(
            NotificationRecipient.user_id == current_user.id,
            Notification.tenant_id == current_user.tenant_id,
            NotificationRecipient.read_at.is_(None),
        )
    ).one()

    return NotificationListResponse(
        items=[build_notification_response(notification, recipient) for notification, recipient in rows],
        unread_count=unread_count,
    )


def mark_notification_as_read(
    *,
    session: Session,
    current_user: User,
    notification_id: UUID,
) -> MarkNotificationReadResponse:
    """현재 사용자 본인이 수신한 특정 알림만 읽음 처리한다."""
    row = session.exec(
        select(Notification, NotificationRecipient)
        .join(
            NotificationRecipient,
            Notification.id == NotificationRecipient.notification_id,
        )
        .where(
            Notification.id == notification_id,
            NotificationRecipient.user_id == current_user.id,
            Notification.tenant_id == current_user.tenant_id,
        )
    ).first()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="알림을 찾을 수 없습니다.",
        )

    _, recipient = row
    now = datetime.utcnow()

    if recipient.read_at is None:
        recipient.read_at = now
        recipient.updated_at = now
        session.add(recipient)
        session.flush()

    return MarkNotificationReadResponse(
        notification_id=notification_id,
        read=True,
        read_at=recipient.read_at,
    )


def mark_all_notifications_as_read(
    *,
    session: Session,
    current_user: User,
) -> MarkAllNotificationsReadResponse:
    """현재 사용자의 읽지 않은 알림을 모두 읽음 처리한다."""
    now = datetime.utcnow()

    result = session.exec(
        update(NotificationRecipient)
        .where(
            NotificationRecipient.user_id == current_user.id,
            NotificationRecipient.read_at.is_(None),
        )
        .values(
            read_at=now,
            updated_at=now,
        )
    )

    return MarkAllNotificationsReadResponse(
        updated_count=result.rowcount or 0,
    )
