import uuid

from fastapi import APIRouter, Depends, status
from sqlmodel import Session

from app.api.dependencies.auth import require_master
from app.core.database import engine
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

# MASTER 전용 직원 계정 생성
@router.post("", response_model = EmployeeCreateResponse, status_code = status.HTTP_201_CREATED,)
def create_employee_account(
    request: EmployeeCreateRequest,
    current_master: User = Depends(require_master),
) -> EmployeeCreateResponse:
    with Session(engine) as session:
        user, temporary_password = create_employee(
            session = session,
            request = request
        )

        return EmployeeCreateResponse(
            id=str(user.id),
            employee_id=user.employee_id,
            email=user.email,
            name=user.name,
            role=user.role,
            status=user.status.value,
            temporary_password=temporary_password,
            must_change_password=user.must_change_password,
        )

# MASTER 전용 사용자 권한 변경
@router.patch("/{user_id}/role", response_model = UserResponse,)   
def change_user_role(
    user_id: uuid.UUID,
    request: UserRoleUpdateRequest,
    current_master: User = Depends(require_master),
) -> UserResponse:
    with Session(engine) as session:
        user = update_user_role(
            session = session,
            target_user_id = user_id,
            current_master_id = current_master.id,
            new_role = request.role,
        )

        return UserResponse(
            id = str(user.id),
            employee_id = user.employee_id,
            email = user.email,
            name = user.name,
            role = user.role,
            status = user.status.value,
            must_change_password = user.must_change_password,
        )


# MASTER 전용 사용자 계정 상태 변경
@router.patch("/{user_id}/status", response_model = UserResponse,)
def change_user_status(
    user_id: uuid.UUID,
    request: UserStatusUpdateRequest,
    current_master: User = Depends(require_master),
) -> UserResponse:
    with Session(engine) as session:
        user = update_user_status(
            session = session,
            target_user_id = user_id,
            current_master_id = current_master.id,
            new_status = request.status,
        )

        return UserResponse(
            id = str(user.id),
            employee_id = user.employee_id,
            email = user.email,
            name = user.name,
            role = user.role,
            status = user.status.value,
            must_change_password = user.must_change_password,
        )