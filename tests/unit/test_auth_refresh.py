from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

from fastapi import Response

from app.domains.auth.auth import (
    delete_refresh_token_cookie,
    set_refresh_token_cookie,
)
from app.core.config import settings
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_refresh_token,
)
from app.models.wms import UserStatus
from app.domains.auth import auth_service


class FakeResult:
    def __init__(self, first_value=None):
        self.first_value = first_value

    def first(self):
        return self.first_value


class FakeSession:
    def __init__(self, user=None):
        self.user = user
        self.executed_statements = []

    def exec(self, statement):
        self.executed_statements.append(str(statement))
        return FakeResult(first_value=self.user)


def build_user(
    *,
    auth_version: int = 0,
    refresh_token_hash: str | None = None,
    refresh_token_expires_at: datetime | None = None,
):
    return SimpleNamespace(
        id=UUID("00000000-0000-4000-8000-000000000001"),
        tenant_id=UUID(
            "00000000-0000-4000-8000-000000000002"
        ),
        status=UserStatus.ACTIVE,
        auth_version=auth_version,
        refresh_token_hash=refresh_token_hash,
        refresh_token_expires_at=refresh_token_expires_at,
        updated_at=None,
        password_hash="old-password-hash",
        must_change_password=True,
    )


def test_login_refresh_session_increments_auth_version(
    monkeypatch,
):
    user = build_user(auth_version=3)

    monkeypatch.setattr(
        auth_service,
        "generate_refresh_token",
        lambda: "login-refresh-token",
    )

    refresh_token = auth_service.create_refresh_session_for_login(
        user=user,
    )

    assert refresh_token == "login-refresh-token"
    assert user.auth_version == 4
    assert user.refresh_token_hash == hash_refresh_token(
        refresh_token
    )
    assert user.refresh_token_expires_at is not None
    assert user.refresh_token_expires_at > datetime.utcnow()


def test_refresh_session_rotation_changes_token_without_auth_version_change(
    monkeypatch,
):
    user = build_user(
        auth_version=4,
        refresh_token_hash=hash_refresh_token(
            "previous-refresh-token"
        ),
        refresh_token_expires_at=(
            datetime.utcnow() + timedelta(days=1)
        ),
    )
    previous_token_hash = user.refresh_token_hash

    monkeypatch.setattr(
        auth_service,
        "generate_refresh_token",
        lambda: "rotated-refresh-token",
    )

    next_refresh_token = auth_service.rotate_refresh_session(
        user=user,
    )

    assert next_refresh_token == "rotated-refresh-token"
    assert user.auth_version == 4
    assert user.refresh_token_hash == hash_refresh_token(
        next_refresh_token
    )
    assert user.refresh_token_hash != previous_token_hash
    assert user.refresh_token_expires_at is not None
    assert user.refresh_token_expires_at > datetime.utcnow()


def test_get_user_by_refresh_token_returns_active_session_user():
    refresh_token = "valid-refresh-token"
    user = build_user(
        refresh_token_hash=hash_refresh_token(refresh_token),
        refresh_token_expires_at=(
            datetime.utcnow() + timedelta(days=1)
        ),
    )
    session = FakeSession(user=user)

    result = auth_service.get_user_by_refresh_token(
        session=session,
        refresh_token=refresh_token,
    )

    assert result is user
    assert any(
        "FOR UPDATE" in statement
        for statement in session.executed_statements
    )


def test_get_user_by_refresh_token_rejects_expired_token():
    refresh_token = "expired-refresh-token"
    user = build_user(
        refresh_token_hash=hash_refresh_token(refresh_token),
        refresh_token_expires_at=(
            datetime.utcnow() - timedelta(seconds=1)
        ),
    )
    session = FakeSession(user=user)

    result = auth_service.get_user_by_refresh_token(
        session=session,
        refresh_token=refresh_token,
    )

    assert result is None


def test_revoke_refresh_session_clears_session_and_invalidates_access_tokens():
    user = build_user(
        auth_version=4,
        refresh_token_hash=hash_refresh_token(
            "active-refresh-token"
        ),
        refresh_token_expires_at=(
            datetime.utcnow() + timedelta(days=1)
        ),
    )

    auth_service.revoke_refresh_session(user=user)

    assert user.refresh_token_hash is None
    assert user.refresh_token_expires_at is None
    assert user.auth_version == 5
    assert user.updated_at is not None


def test_password_change_revokes_refresh_session(
    monkeypatch,
):
    user = build_user(
        auth_version=4,
        refresh_token_hash=hash_refresh_token(
            "active-refresh-token"
        ),
        refresh_token_expires_at=(
            datetime.utcnow() + timedelta(days=1)
        ),
    )
    saved_users = []

    verify_results = iter([True, False])

    monkeypatch.setattr(
        auth_service,
        "verify_password",
        lambda **_kwargs: next(verify_results),
    )
    monkeypatch.setattr(
        auth_service,
        "hash_password",
        lambda _password: "new-password-hash",
    )
    monkeypatch.setattr(
        auth_service,
        "save_user",
        lambda **kwargs: saved_users.append(kwargs["user"]),
    )

    auth_service.change_password(
        session=SimpleNamespace(),
        user=user,
        current_password="current-password",
        new_password="new-password",
    )

    assert user.password_hash == "new-password-hash"
    assert user.must_change_password is False
    assert user.refresh_token_hash is None
    assert user.refresh_token_expires_at is None
    assert user.auth_version == 5
    assert saved_users == [user]


def test_access_token_contains_current_auth_version():
    access_token = create_access_token(
        subject="00000000-0000-4000-8000-000000000001",
        role="ADMIN",
        tenant_id="00000000-0000-4000-8000-000000000002",
        auth_version=7,
    )

    payload = decode_access_token(access_token)

    assert payload is not None
    assert payload["type"] == "access"
    assert payload["auth_version"] == 7


def test_refresh_token_cookie_is_http_only_and_scoped_to_auth_api():
    response = Response()

    set_refresh_token_cookie(
        response=response,
        refresh_token="refresh-token-value",
    )

    cookie_header = response.headers["set-cookie"].lower()

    assert (
        f"{settings.REFRESH_TOKEN_COOKIE_NAME}=refresh-token-value"
        in cookie_header
    )
    assert "httponly" in cookie_header
    assert "path=/api/v1/auth" in cookie_header


def test_delete_refresh_token_cookie_expires_cookie():
    response = Response()

    delete_refresh_token_cookie(response=response)

    cookie_header = response.headers["set-cookie"].lower()

    assert settings.REFRESH_TOKEN_COOKIE_NAME in cookie_header
    assert "max-age=0" in cookie_header