from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.wms import (
    NotificationCategory,
    NotificationSeverity,
)


class NotificationResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": ("00000000-0000-4000-8000-000000000001"),
                "category": "FDS_ALERT",
                "severity": "HIGH",
                "title": "반품 이상 패턴 감지",
                "message": ("최근 반품 비율이 기준치를 초과했습니다."),
                "timestamp": "2026-07-28T10:00:00+09:00",
                "read": False,
            }
        }
    )

    id: UUID = Field(description="알림 ID")
    category: NotificationCategory = Field(
        description="알림 업무 유형",
    )
    severity: NotificationSeverity = Field(
        description="알림 중요도",
    )
    title: str = Field(description="알림 제목")
    message: str = Field(description="알림 본문")
    timestamp: datetime = Field(description="알림 생성 시각")
    read: bool = Field(description="현재 사용자의 읽음 여부")
    payload: dict[str, Any] | None = Field(
        default=None,
        description="알림 유형별 상세 화면 이동 및 표시용 데이터",
    )


class NotificationListResponse(BaseModel):
    items: list[NotificationResponse] = Field(
        default_factory=list,
        description="알림 목록",
    )
    unread_count: int = Field(
        ge=0,
        description="현재 사용자의 읽지 않은 알림 수",
    )


class MarkNotificationReadResponse(BaseModel):
    notification_id: UUID = Field(
        description="읽음 처리한 알림 ID",
    )
    read: bool = Field(
        description="읽음 처리 결과",
    )
    read_at: datetime = Field(
        description="읽음 처리 시각",
    )


class MarkAllNotificationsReadResponse(BaseModel):
    updated_count: int = Field(
        ge=0,
        description="이번 요청에서 읽음 처리된 알림 수",
    )


class NotificationStreamTicketResponse(BaseModel):
    ticket: str = Field(
        description="알림 SSE 연결용 단기 티켓",
    )
    expires_in: int = Field(
        description="티켓 만료까지 남은 시간(초)",
    )
