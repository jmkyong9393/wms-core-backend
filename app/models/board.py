import uuid
from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import Column, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.models.enums import *


class Notification(SQLModel, table=True):
    __tablename__ = "notifications"

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        primary_key=True,
    )
    tenant_id: UUID = Field(
        foreign_key="tenants.id",
        nullable=False,
        index=True,
    )
    category: NotificationCategory = Field(
        nullable=False,
    )
    severity: NotificationSeverity = Field(
        nullable=False,
    )
    title: str = Field(
        max_length=200,
        nullable=False,
    )
    message: str = Field(
        nullable=False,
    )
    payload: dict = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False),
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
    )


class NotificationRecipient(SQLModel, table=True):
    __tablename__ = "notification_recipients"
    __table_args__ = (
        UniqueConstraint(
            "notification_id",
            "user_id",
            name="uq_notification_recipient",
        ),
    )
    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        primary_key=True,
    )
    notification_id: UUID = Field(
        foreign_key="notifications.id",
        nullable=False,
        index=True,
    )
    user_id: UUID = Field(
        foreign_key="users.id",
        nullable=False,
        index=True,
    )
    read_at: Optional[datetime] = Field(
        default=None,
        index=True,
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
    )


class Board(SQLModel, table=True):
    __tablename__ = "boards"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    job_id: uuid.UUID = Field(foreign_key="return_jobs.id")
    ticket_status: TicketStatus = Field(nullable=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class BoardPost(SQLModel, table=True):
    __tablename__ = "board_posts"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    author_id: uuid.UUID = Field(foreign_key="users.id")
    category: PostCategory = Field(nullable=False)
    title: str = Field(nullable=False)
    content: str = Field(nullable=False)
    attachment_paths: Optional[list] = Field(default=None, sa_column=Column(JSONB))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
