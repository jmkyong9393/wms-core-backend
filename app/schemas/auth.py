from datetime import date
from uuid import UUID

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


# 관리자용 직원 계정 생성 요청
class EmployeeCreateRequest(AuthSchema):
    name: str = Field(
        min_length=2,
        max_length=50,
    )
    email: EmailStr | None = None
    hire_date: date

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
    role: UserRole

# 사용자 계정 상태 변경 요청
class UserStatusUpdateRequest(BaseModel):
    status: UserStatus