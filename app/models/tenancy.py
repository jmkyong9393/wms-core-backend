import uuid
from uuid import UUID
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from sqlalchemy import CheckConstraint, Column, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import Numeric
from sqlmodel import Field, SQLModel

from app.models.enums import *  # noqa: F401,F403


class Tenant(SQLModel, table=True):
    __tablename__ = "tenants"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    code: str = Field(max_length=50, unique=True, index=True, nullable=False)
    name: str = Field(max_length=100, nullable=False)
    is_active: bool = Field(default=True, nullable=False)
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    tenant_id: UUID = Field(foreign_key="tenants.id", nullable=False, index=True, )
    employee_id: str = Field(    # 사번은 필수값, 중복 불가로 변경
        nullable=False,
        unique=True,
        index=True,
    )
    email: Optional[str] = Field(    # 이메일 중복 불가로 변경
        default=None,
        unique=True,
        index=True,
    )
    name: str = Field(nullable=False)
    password_hash: str = Field(nullable=False)
    role: UserRole = Field(nullable=False)
    status: UserStatus = Field(default=UserStatus.ACTIVE)

    # 관리자가 직원 계정 만들면 True -> 직원이 임시 비밀번호를 새 비밀번호로 변경하면 False
    must_change_password: bool = Field(default=True)

    refresh_token_hash: Optional[str] = Field(default=None, unique=True,)
    refresh_token_expires_at: Optional[datetime] = Field(default=None,index=True,)
    auth_version: int = Field(default=0, nullable=False,)

    last_login: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
