from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session

from app.core.database import get_session
from app.core.exceptions import (
    AdminPermissionRequiredException,
    InactiveUserException,
    MasterPermissionRequiredException,
    PasswordChangeRequiredException,
)
from app.core.security import decode_access_token
from app.models.wms import Tenant, User, UserRole, UserStatus

bearer_scheme = HTTPBearer(
    auto_error=False,
)


# JWT 인증 실패 응답 생성 Exception
def unauthorized_exception(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


# JWT와 계정 상태만 검증
# 최초 비밀번호 변경 전에도 비밀번호 변경 API에서는 사용할 수 있음.
def get_authenticated_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: Session = Depends(get_session),
) -> User:
    # 1. 인증 토큰 검증
    if credentials is None:
        raise unauthorized_exception("인증 토큰이 필요합니다.")

    # 2. JWT 검증
    payload = decode_access_token(credentials.credentials)

    if payload is None:
        raise unauthorized_exception("유효하지 않거나 만료된 토큰입니다.")

    subject = payload.get("sub")
    tenant_subject = payload.get("tenant_id")

    # 3. payload의 sub, tenant_id에서 사용자 UUID 확인
    if subject is None:
        raise unauthorized_exception("토큰에 사용자 정보가 없습니다.")

    if tenant_subject is None:
        raise unauthorized_exception("토큰에 테넌트 정보가 없습니다.")

    try:
        user_id = UUID(str(subject))
        token_tenant_id = UUID(str(tenant_subject))
    except (TypeError, ValueError) as error:
        raise unauthorized_exception("토큰의 사용자 또는 테넌트 정보가 올바르지 않습니다.") from error

    # 4. DB에서 사용자 조회
    user = session.get(User, user_id)

    if user is None:
        raise unauthorized_exception("인증된 사용자를 찾을 수 없습니다.")

    # JWT 발급 시점의 인증 버전과 현재 사용자 인증 버전을 비교한다.
    # 새 로그인·로그아웃·비밀번호 변경 후에는 기존 Access Token을 즉시 무효화한다.
    token_auth_version = payload.get("auth_version")

    if (
        isinstance(token_auth_version, bool)
        or not isinstance(token_auth_version, int)
        or token_auth_version != user.auth_version
    ):
        raise unauthorized_exception("만료되었거나 무효화된 토큰입니다.")

    if user.tenant_id != token_tenant_id:
        raise unauthorized_exception("토큰의 테넌트 정보가 사용자 소속과 일치하지 않습니다.")

    tenant = session.get(Tenant, token_tenant_id)

    if tenant is None or not tenant.is_active:
        raise unauthorized_exception("비활성화되었거나 존재하지 않는 테넌트입니다.")

    if user.status != UserStatus.ACTIVE:
        raise InactiveUserException()

    return user


# 일반 보호 API 에서 사용
# 최초 비밀번호를 변경하지 않은 사용자는 접근 차단
def get_current_user(
    authenticated_user: User = Depends(get_authenticated_user),
) -> User:
    if authenticated_user.must_change_password:
        raise PasswordChangeRequiredException()

    return authenticated_user


# MASTER 권한 사용자 확인
def require_master(
    current_user: User = Depends(get_current_user),
) -> User:
    if current_user.role != UserRole.MASTER:
        raise MasterPermissionRequiredException()

    return current_user


# ADMIN 권한 사용자 확인
def require_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    if current_user.role != UserRole.ADMIN:
        raise AdminPermissionRequiredException()

    return current_user


# MASTER 또는 ADMIN 권한 사용자 확인
def require_admin_or_master(
    current_user: User = Depends(get_current_user),
) -> User:
    if current_user.role not in {
        UserRole.MASTER,
        UserRole.ADMIN,
    }:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="MASTER 또는 ADMIN 권한이 필요합니다.",
        )

    return current_user


# WMS 내부 재고 및 로케이션 업무를 수행할 수 있는 사용자 확인
def require_wms_operator(
    current_user: User = Depends(get_current_user),
) -> User:
    if current_user.role not in {
        UserRole.MASTER,
        UserRole.ADMIN,
        UserRole.WORKER,
    }:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="WMS 작업자 권한이 필요합니다.",
        )

    return current_user
