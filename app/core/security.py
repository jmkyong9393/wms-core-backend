import secrets
import string
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from jwt import InvalidTokenError
from pwdlib import PasswordHash

from app.core.config import settings

MIN_TEMPORARY_PASSWORD_LENGTH = 12
DEFAULT_TEMPORARY_PASSWORD_LENGTH = 14
SPECIAL_CHARACTERS = "!@#$%"

password_hash = PasswordHash.recommended()

# 안전한 임시 비밀번호 생성
def generate_temporary_password(length: int = DEFAULT_TEMPORARY_PASSWORD_LENGTH) -> str:

    if length < MIN_TEMPORARY_PASSWORD_LENGTH:
        raise ValueError("임시 비밀번호 길이는 12자 이상이어야 합니다.")
    
    required_characters = [
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.digits),
        secrets.choice(SPECIAL_CHARACTERS),
    ]

    all_characters = (
        string.ascii_lowercase
        + string.ascii_uppercase
        + string.digits
        + SPECIAL_CHARACTERS
    )

    remaining_characters = [
        secrets.choice(all_characters)
        for _ in range(length - len(required_characters))
    ]

    password_characters = required_characters + remaining_characters

    secrets.SystemRandom().shuffle(password_characters)

    return "".join(password_characters)

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
    tenant_id: str,
    expiration_minutes: int | None = None,
) -> str:
    expiration_minutes = (
        expiration_minutes
        if expiration_minutes is not None
        else settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )

    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes = expiration_minutes
    )

    payload: dict[str, Any] = {
        "sub": subject,
        "role": role,
        "tenant_id": tenant_id,
        "type": "access",
        "exp": expires_at,
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
    except InvalidTokenError:
        return None
    
    if payload.get("type") != "access":
        return None
    
    return payload
    
