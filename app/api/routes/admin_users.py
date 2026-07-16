from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlmodel import Session

from app.api.dependencies.auth import require_master
from app.core.database import get_session
from app.models.wms import User
from app.schemas.auth import (
    EmployeeCreateRequest,
    EmployeeCreateResponse,
    UserResponse,
    UserRoleUpdateRequest,
    UserStatusUpdateRequest,
)
from app.services.auth_service import (
    create_employee,
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

# MASTER 전용 직원 계정 생성
@router.post(
    "/create-accounts",
    response_model=EmployeeCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_employee_account(
    request: EmployeeCreateRequest,
    _current_master: User = Depends(require_master),
    session: Session = Depends(get_session),
) -> EmployeeCreateResponse:
    user, temporary_password = create_employee(
        session=session,
        request=request,
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
        current_master_id=current_master.id,
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
        current_master_id=current_master.id,
        new_status=request.status,
    )

    return build_user_response(user)