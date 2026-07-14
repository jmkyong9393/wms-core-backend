from enum import Enum

from pydantic import BaseModel, EmailStr, Field

class SignupRole(str, Enum):
    WORKER = "WORKER"
    MASTER = "MASTER"

# 회원가입
class SignupRequest(BaseModel):
    # 사번
    employee_id: str = Field(
        min_length=2,
        max_length=50,
    )
    # 이름
    name: str = Field(
        min_length=2,
        max_length=50,
    )
    # 이메일 (선택)
    email: EmailStr | None = None

    # 비밀번호
    password: str = Field(
        min_length=8,
        max_length=100,
    )
    
    # role
    role: SignupRole

    # 가입 제한 코드
    security_code: str = Field(
        min_length=4,
        max_length=100,
    )

# 로그인
class LoginRequest(BaseModel):
    # 사번
    employee_id: str = Field(
        min_length=2,
        max_length=50
    )
    # 비밀번호
    password: str = Field(
        min_length=8,
        max_length=100,
    )

# 로그인 성공 후 백엔드가 JWT 반환하는 형식
class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int