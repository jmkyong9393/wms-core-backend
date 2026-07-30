from datetime import datetime

from fastapi import (
    APIRouter,
    Depends,
    Request,
    Response,
    status,
)
from sqlmodel import Session

from app.api.dependencies.auth import (
    get_authenticated_user,
    get_current_user,
    unauthorized_exception,
)
from app.core.config import settings
from app.core.database import get_session
from app.core.exceptions import UserNotFoundException
from app.core.security import create_access_token
from app.models.wms import (
    Tenant,
    User,
    UserStatus,
)
from app.schemas.auth import (
    LoginRequest,
    PasswordChangeRequest,
    TokenResponse,
    UserResponse,
)
from app.services.auth_service import (
    authenticate_user,
    change_password,
    create_refresh_session_for_login,
    get_user_by_refresh_token,
    revoke_refresh_session,
    rotate_refresh_session,
)


router = APIRouter()


def set_refresh_token_cookie(
    response: Response,
    refresh_token: str,
) -> None:
    """현재 브라우저의 단일 로그인 세션용 Refresh Token Cookie를 설정한다."""
    response.set_cookie(
        key=settings.REFRESH_TOKEN_COOKIE_NAME,
        value=refresh_token,
        max_age=(
            settings.REFRESH_TOKEN_EXPIRE_DAYS
            * 24
            * 60
            * 60
        ),
        httponly=True,
        secure=settings.REFRESH_TOKEN_COOKIE_SECURE,
        samesite=settings.REFRESH_TOKEN_COOKIE_SAMESITE,
        path="/api/v1/auth",
    )


def delete_refresh_token_cookie(
    response: Response,
) -> None:
    """현재 브라우저의 Refresh Token Cookie를 삭제한다."""
    response.delete_cookie(
        key=settings.REFRESH_TOKEN_COOKIE_NAME,
        httponly=True,
        secure=settings.REFRESH_TOKEN_COOKIE_SECURE,
        samesite=settings.REFRESH_TOKEN_COOKIE_SAMESITE,
        path="/api/v1/auth",
    )


def validate_refresh_user(
    session: Session,
    user: User,
) -> None:
    """Refresh Token으로 조회한 사용자의 현재 계정·테넌트 상태를 검증한다."""
    if user.status != UserStatus.ACTIVE:
        raise unauthorized_exception(
            "비활성화된 사용자입니다."
        )

    tenant = session.get(Tenant, user.tenant_id)

    if tenant is None or not tenant.is_active:
        raise unauthorized_exception(
            "비활성화되었거나 존재하지 않는 테넌트입니다."
        )


# 로그인 API
# 사번과 비밀번호를 검증하고 Access Token과 Refresh Token Cookie를 발급한다.
@router.post(
    "/login",
    response_model=TokenResponse,
)
def login(
    request: LoginRequest,
    response: Response,
    session: Session = Depends(get_session),
) -> TokenResponse:
    user = authenticate_user(
        session=session,
        employee_id=request.employee_id,
        password=request.password,
    )

    # 새 로그인은 기존 브라우저/기기의 활성 세션을 무효화한다.
    refresh_token = create_refresh_session_for_login(
        user=user,
    )

    access_token = create_access_token(
        subject=str(user.id),
        role=user.role.value,
        tenant_id=str(user.tenant_id),
        auth_version=user.auth_version,
    )

    user.last_login = datetime.utcnow()

    session.add(user)
    session.commit()
    session.refresh(user)

    set_refresh_token_cookie(
        response=response,
        refresh_token=refresh_token,
    )

    return TokenResponse(
        access_token=access_token,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        must_change_password=user.must_change_password,
    )


# Refresh Token Cookie로 새 Access Token을 발급하고 Refresh Token도 회전한다.
@router.post(
    "/refresh",
    response_model=TokenResponse,
)
def refresh_access_token(
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
) -> TokenResponse:
    refresh_token = request.cookies.get(
        settings.REFRESH_TOKEN_COOKIE_NAME
    )

    if not refresh_token:
        raise unauthorized_exception(
            "Refresh Token이 필요합니다."
        )

    user = get_user_by_refresh_token(
        session=session,
        refresh_token=refresh_token,
    )

    if user is None:
        raise unauthorized_exception(
            "유효하지 않거나 만료된 Refresh Token입니다."
        )

    validate_refresh_user(
        session=session,
        user=user,
    )

    # 갱신할 때마다 Refresh Token을 회전해 기존 Cookie 값을 즉시 무효화한다.
    next_refresh_token = rotate_refresh_session(
        user=user,
    )

    access_token = create_access_token(
        subject=str(user.id),
        role=user.role.value,
        tenant_id=str(user.tenant_id),
        auth_version=user.auth_version,
    )

    session.add(user)
    session.commit()
    session.refresh(user)

    set_refresh_token_cookie(
        response=response,
        refresh_token=next_refresh_token,
    )

    return TokenResponse(
        access_token=access_token,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        must_change_password=user.must_change_password,
    )


# 현재 브라우저의 Refresh Token 세션을 폐기하고 Cookie를 삭제한다.
@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
)
def logout(
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
) -> None:
    refresh_token = request.cookies.get(
        settings.REFRESH_TOKEN_COOKIE_NAME
    )

    if refresh_token:
        user = get_user_by_refresh_token(
            session=session,
            refresh_token=refresh_token,
        )

        if user is not None:
            revoke_refresh_session(user=user)
            session.add(user)
            session.commit()

    delete_refresh_token_cookie(response)