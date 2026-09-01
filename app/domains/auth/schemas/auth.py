from datetime import date, datetime
from typing import Literal
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


class EmployeeBulkCreateRow(AuthSchema):
    """
    일괄 계정 생성 엑셀의 검증 완료 행.

    source_row는 엑셀에서 오류가 발생했을 때
    관리자가 어느 행을 수정해야 하는지 안내하기 위한 행 번호다.
    """

    source_row: int = Field(
        ge=2,
        description="헤더를 제외한 원본 엑셀 행 번호",
    )

    name: str = Field(
        min_length=2,
        max_length=50,
        description="직원 이름",
    )

    hire_date: date = Field(
        description="입사일. 사번의 YYMM 부분 생성 기준",
    )

    role: Literal[
        UserRole.ADMIN,
        UserRole.WORKER,
    ] = Field(
        description="직원 역할",
    )

    email: EmailStr | None = Field(
        default=None,
        description="직원 이메일. 비어 있으면 null",
    )


class EmployeeBulkCreateResultRow(EmployeeBulkCreateRow):
    """
    일괄 계정 생성 완료 후 결과 엑셀에 기록할 행.

    temporary_password는 생성 직후 결과 파일에만 기록하며,
    DB에는 해시값만 저장한다.
    """

    employee_id: str = Field(
        description="발급된 사번. 예: NZ2608001",
    )

    temporary_password: str = Field(
        description="최초 로그인용 임시 비밀번호",
    )


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
