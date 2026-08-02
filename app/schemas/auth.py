from datetime import date, datetime
from datetime import date
from uuid import UUID
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.wms import UserRole, UserStatus

class AuthSchema(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
    )

# 로그인 요청
class LoginRequest(BaseModel):
    employee_id: str = Field(
        min_length=2,
        max_length=50,
    )
    password: str = Field(
        min_length=8,
        max_length=100,
    )

# 로그인 성공 시 JWT 응답
class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    must_change_password: bool


# 관리자, 직원 계정 생성 요청
class EmployeeCreateRequest(AuthSchema):
    name: str = Field(
        min_length=2,
        max_length=50,
    )
    email: EmailStr | None = None
    hire_date: date
    role: Literal[
        UserRole.ADMIN,
        UserRole.WORKER,
    ] = UserRole.WORKER


# 현재 사용자 정보 응답
class UserResponse(AuthSchema):
    id: UUID
    employee_id: str
    email: EmailStr | None
    name: str
    role: UserRole
    status: UserStatus
    must_change_password: bool


# 직원 계정 생성 결과 응답
class EmployeeCreateResponse(UserResponse):
    temporary_password: str

class EmployeeListItemResponse(AuthSchema):
    """
    MASTER 직원 관리 화면의 목록 행.

    권한·상태 변경 API에 사용할 사용자 UUID를 포함한다.
    비밀번호 해시와 Refresh Token 등 민감한 인증 정보는 반환하지 않는다.
    """

    id: UUID
    employee_id: str
    email: EmailStr | None
    name: str
    role: UserRole
    status: UserStatus
    must_change_password: bool
    created_at: datetime


class EmployeeListResponse(AuthSchema):
    """
    직원 관리 그리드의 서버 페이지네이션 응답.
    """

    items: list[EmployeeListItemResponse] = Field(
        default_factory=list,
    )
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    size: int = Field(ge=1)
    total_pages: int = Field(ge=0)

# 비밀번호 변경 요청
class PasswordChangeRequest(BaseModel):
    current_password: str = Field(
        min_length=8,
        max_length=100,
    )
    new_password: str = Field(
        min_length=8,
        max_length=100,
    )

# 사용자 권한 변경 요청
class UserRoleUpdateRequest(BaseModel):
    role: Literal[
        UserRole.ADMIN,
        UserRole.WORKER,
    ]

# 사용자 계정 상태 변경 요청
class UserStatusUpdateRequest(BaseModel):
    status: UserStatus