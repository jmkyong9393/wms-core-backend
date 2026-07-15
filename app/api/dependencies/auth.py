from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from sqlmodel import Session

from app.core.database import get_session
from app.core.exceptions import (
    InactiveUserException,
    MasterPermissionRequiredException,
    PasswordChangeRequiredException,
)
from app.core.security import decode_access_token
from app.models.wms import User, UserRole, UserStatus

bearer_scheme = HTTPBearer(auto_error = False,)

# JWT 인증 실패 응답 생성 Exception
def unauthorized_exception(detail: str) -> HTTPException:
    return HTTPException(
        status_code = status.HTTP_401_UNAUTHORIZED,
        detail = detail,
        headers = {"WWW-Authenticate" : "Bearer"},
    )


# Authorization 헤더의 JWT를 검증하고 현재 로그인한 사용자를 반환하는 함수
def get_current_user(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
        session: Session = Depends(get_session),
) -> User:
    
    if credentials is None:
        raise unauthorized_exception(
            "인증 토큰이 필요합니다."
        )
    
    # 1. JWT 검증
    payload = decode_access_token(credentials.credentials)

    if payload is None:
        raise unauthorized_exception(
            "유효하지 않거나 만료된 토큰입니다."
        )
    
    subject = payload.get("sub") 

    # 2. payload의 sub에서 사용자 UUID 확인
    if subject is None:
        raise unauthorized_exception(
            "토큰에 사용자 정보가 없습니다."
        )
    
    try:
        user_id = UUID(str(subject))
    except (TypeError,ValueError) : 
        raise unauthorized_exception(
            "토큰의 사용자 정보가 올바르지 않습니다."
        )

    # 3. DB에서 사용자 조회
    user = session.get(User, user_id)

    if user is None:
        raise unauthorized_exception(
        "인증된 사용자를 찾을 수 없습니다."
    )
    
    # 4. ACTIVE 계정인지 확인
    if user.status != UserStatus.ACTIVE:
        raise InactiveUserException()
    
    return user

# MASTER 권한 사용자 확인
def require_master(
    current_user: User = Depends(get_current_user),
) -> User:
    if current_user.role != UserRole.MASTER:
        raise MasterPermissionRequiredException()

    return current_user

# 최초 비밀번호 변경 완료 사용자 확인
def require_password_changed(
        current_user: User = Depends(get_current_user),
) -> User:
    if current_user.must_change_password:
        raise PasswordChangeRequiredException()
    
    return current_user


