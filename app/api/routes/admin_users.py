from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlmodel import Session

from app.api.dependencies.auth import require_master
from app.core.database import get_session
from app.models.wms import (
    User,
    UserRole,
    UserStatus,
)
from app.schemas.auth import (
    EmployeeCreateRequest,
    EmployeeCreateResponse,
    EmployeeListResponse,
    UserResponse,
    UserRoleUpdateRequest,
    UserStatusUpdateRequest,
)
from app.services.auth_service import (
    create_employee,
    list_employees,
    update_user_role,
    update_user_status,
)

router = APIRouter()

# 권한.상태 변경 응답 반복 제거
def build_user_response(
    user: User,
) -> UserResponse:
    return UserResponse(
        id=user.id,
        employee_id=user.employee_id,
        email=user.email,
        name=user.name,
        role=user.role,
        status=user.status,
        must_change_password=user.must_change_password,
    )

@router.get(
    "",
    response_model=EmployeeListResponse,
    summary="MASTER 직원 계정 목록 조회",
)
def get_employee_accounts(
    keyword: str | None = Query(
        default=None,
        description="사번·이름·이메일 부분 검색",
    ),
    role: UserRole | None = Query(
        default=None,
        description="역할 필터",
    ),
    status: UserStatus | None = Query(
        default=None,
        description="계정 상태 필터",
    ),
    page: int = Query(
        default=1,
        ge=1,
        description="페이지 번호",
    ),
    size: int = Query(
        default=20,
        ge=1,
        le=100,
        description="페이지당 조회 건수",
    ),
    current_master: User = Depends(require_master),
    session: Session = Depends(get_session),
) -> EmployeeListResponse:
    """
    현재 MASTER와 같은 테넌트에 속한 직원 계정을 조회한다.

    목록의 id는 권한·상태 변경 API에서 사용하는 User UUID이며,
    비밀번호와 Refresh Token 같은 민감한 인증 정보는 노출하지 않는다.
    """
    return list_employees(
        session=session,
        tenant_id=current_master.tenant_id,
        keyword=keyword,
        role=role,
        status=status,
        page=page,
        size=size,
    )

# MASTER 전용 직원 계정 생성
@router.post(
    "/create-accounts",
    response_model=EmployeeCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_employee_account(
    request: EmployeeCreateRequest,
    current_master: User = Depends(require_master),
    session: Session = Depends(get_session),
) -> EmployeeCreateResponse:
    user, temporary_password = create_employee(
        session=session,
        request=request,
        current_master=current_master,
    )

    return EmployeeCreateResponse(
        id=user.id,
        employee_id=user.employee_id,
        email=user.email,
        name=user.name,
        role=user.role,
        status=user.status,
        temporary_password=temporary_password,
        must_change_password=user.must_change_password,
    )

# MASTER 전용 사용자 권한 변경
@router.patch(
    "/{user_id}/role",
    response_model=UserResponse,
)
def change_user_role(
    user_id: UUID,
    request: UserRoleUpdateRequest,
    current_master: User = Depends(require_master),
    session: Session = Depends(get_session),
) -> UserResponse:
    user = update_user_role(
        session=session,
        target_user_id=user_id,
        current_master=current_master,
        new_role=request.role,
    )

    return build_user_response(user)


# MASTER 전용 사용자 계정 상태 변경
@router.patch(
    "/{user_id}/status",
    response_model=UserResponse,
)
def change_user_status(
    user_id: UUID,
    request: UserStatusUpdateRequest,
    current_master: User = Depends(require_master),
    session: Session = Depends(get_session),
) -> UserResponse:
    user = update_user_status(
        session=session,
        target_user_id=user_id,
        current_master=current_master,
        new_status=request.status,
    )

    return build_user_response(user)