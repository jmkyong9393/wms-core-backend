import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from sqlmodel import Session

from app.core.database import engine
from app.core.security import decode_access_token
from app.models.wms import User, UserStatus

bearer_scheme = HTTPBearer()

# Authorization 헤더의 JWT를 검증하고 현재 로그인한 사용자를 반환하는 함수
def get_current_user(
        credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> User:
    token = credentials.credentials

    # 1. JWT 검증
    payload = decode_access_token(token)

    if payload is None:
        raise HTTPException(
            status_code = status.HTTP_401_UNAUTHORIZED,
            detail = "유효하지 않거나 만료된 토큰입니다.",
            headers = {"WWW-Authenticate" : "Bearer"}
        )
    
    subject = payload.get("sub") # 사용자 id

    # 2. payload의 sub에서 사용자 UUID 확인
    if subject is None:
        raise HTTPException(
            status_code = status.HTTP_401_UNAUTHORIZED,
            detail = "토큰에 사용자 정보가 없습니다.",
            headers = {"WWW-Authenticate": "Bearer"},
        )
    
    try:
        user_id = uuid.UUID(subject)
    except ValueError : 
        raise HTTPException(
            status_code = status.HTTP_401_AUTHORIZED,
            detail = "토큰의 사용자 정보가 옳지 않습니다.",
            headers = {"WWW-Authenticate": "Bearer"}
        )

    # 3. DB에서 사용자 조회
    with Session(engine) as session:
        user = session.get(User, user_id)

        if user is None:
            raise HTTPException(
                status_code = status.HTTP_401_UNAUTHORIZED,
                detail = "사용자를 찾을 수 엇습니다.",
                headers = {"WWW-Authenticate": "Bearer"}
            )
        
        # 4. ACTIVE 계정인지 확인
        if user.status == UserStatus.INACTIVE:
            raise HTTPException(
                status_code = status.HTTP_403_FORBIDDEN,
                detail="비활성화된 계정입니다.",
            )
        
        session.expunge(user)

        # 현재 User 반환
        return user

