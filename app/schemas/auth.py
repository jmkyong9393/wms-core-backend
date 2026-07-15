from datetime import date
from pydantic import BaseModel, EmailStr, Field

from app.models.wms import UserRole, UserStatus

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
class EmployeeCreateRequest(BaseModel):
    name: str = Field(
        min_length=2,
        max_length=50,
    )
    email: EmailStr | None = None
    hire_date: date


# 직원 계정 생성 결과 응답
class EmployeeCreateResponse(BaseModel):
    id: str
    employee_id: str
    email: EmailStr | None
    name: str
    role: UserRole
    status: str
    temporary_password: str
    must_change_password: bool

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

# 현재 사용자 정보 응답
class UserResponse(BaseModel):
    id: str
    employee_id: str
    email: EmailStr | None
    name: str
    role: UserRole
    status: str
    must_change_password: bool

# 사용자 권한 변경 요청
class UserRoleUpdateRequest(BaseModel):
    role: UserRole

# 사용자 계정 상태 변경 요청
class UserStatusUpdateRequest(BaseModel):
    status: UserStatus