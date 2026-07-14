from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from jwt import InvalidTokenError
from pwdlib import PasswordHash

from app.core.config import settings

password_hash = PasswordHash.recommended()

# 사용자가 입력한 비밀번호를 안전한 해시 문자열로 변환하는 함수
def hash_password(password: str) -> str :
    return password_hash.hash(password)

# 사용자가 입력한 비밀번호와 DB에 저장된 해시값이 일치하는지 확인
def verify_password(plain_password:str, hashed_password: str) -> bool:
    return password_hash.verify(
        plain_password,
        hashed_password,
    )

# 사용자가 ID와 권한을 포함한 JWT Access Token을 생성한다. 
def create_access_token(
        subject: str,
        role: str,
        expires_minutes: int | None = None,
) -> str:
    expires_minutes = (
        expires_minutes
        if expires_minutes is not None
        else settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )

    expire = datetime.now(timezone.utc) + timedelta(
        minutes = expires_minutes
    )

    payload: dict[str, Any] = {
        "sub": subject,  # 사용자 UUID
        "role" : role,   # WORKER
        "type": "access", # access
        "exp": expire,    # 만료 시각
    }

    return jwt.encode(
        payload,
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )

# 이후 요청에서 토큰이 정상인지 검증하는 함수
def decode_access_token(token: str) -> dict[str,Any] | None:
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )

        if payload.get("type") != "access":
            return None
        
        return payload
    
    except InvalidTokenError:
        return None
    
