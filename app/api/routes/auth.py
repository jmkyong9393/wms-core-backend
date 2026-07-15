from datetime import datetime

from fastapi import APIRouter, Depends, status
from sqlmodel import Session

from app.api.dependencies.auth import get_current_user
from app.core.config import settings
from app.core.database import get_session
from app.core.exceptions import UserNotFoundException
from app.core.security import create_access_token
from app.models.wms import User
from app.schemas.auth import (
        LoginRequest,
        PasswordChangeRequest,
        TokenResponse,
        UserResponse,
)
from app.services.auth_service import (
        authenticate_user,
        change_password,
)


router = APIRouter()


# 로그인 api
# 사번과 비밀번호를 검증하고 JWT Access Token 발급함.
@router.post("/login", response_model = TokenResponse,)
def login(
        request: LoginRequest,
          session: Session = Depends(get_session),
        ) -> TokenResponse:    
        user = authenticate_user(
                session = session,
                employee_id = request.employee_id,
                password =  request.password,
        )

        access_token = create_access_token(
                subject = str(user.id),
                role = user.role.value,
        )

        user.last_login = datetime.utcnow()

        session.add(user)
        session.commit()
        session.refresh(user)

        return TokenResponse(
                access_token=access_token,
                expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
                must_change_password=user.must_change_password,
        )

# 현재 비밀번호 확인 후 새 비밀번호로 변경
@router.patch("/password", status_code = status.HTTP_204_NO_CONTENT)
def update_password(
        request: PasswordChangeRequest,
        current_user: User = Depends(get_current_user),
        session: Session = Depends(get_session),
)-> None:
        user = session.get(User, current_user.id)

        if user is None:
                raise UserNotFoundException()
                
        change_password(
                session = session,
                user = user,
                current_password = request.current_password,
                new_password = request.new_password,
        )

# 현재 로그인한 사용자 정보 반환하는 api
@router.get(
    "/me",
    response_model=UserResponse,
)
def get_me(
    current_user: User = Depends(get_current_user),
) -> UserResponse:
    return UserResponse(
        id=current_user.id,
        employee_id=current_user.employee_id,
        email=current_user.email,
        name=current_user.name,
        role=current_user.role,
        status=current_user.status,
        must_change_password=current_user.must_change_password,
    )
