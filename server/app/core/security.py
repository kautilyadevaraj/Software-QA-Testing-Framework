from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
import hashlib

import bcrypt
from jose import JWTError, jwt
from starlette.responses import Response

from app.core.config import get_settings


def _prepare_password(password: str) -> bytes:
    """SHA-256 pre-hash so any password length works safely with bcrypt's 72-byte limit."""
    return hashlib.sha256(password.encode("utf-8")).hexdigest().encode("utf-8")


def hash_password(password: str) -> str:
    hashed = bcrypt.hashpw(_prepare_password(password), bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(_prepare_password(password), password_hash.encode("utf-8"))


def _encode_token(subject: str, token_type: str, expires_delta: timedelta) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(subject: str) -> str:
    settings = get_settings()
    return _encode_token(subject, "access", timedelta(minutes=settings.access_token_expire_minutes))


def create_refresh_token(subject: str) -> str:
    settings = get_settings()
    return _encode_token(subject, "refresh", timedelta(days=settings.refresh_token_expire_days))


def decode_token(token: str, expected_type: str) -> dict[str, Any] | None:
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None

    token_type = payload.get("type")
    subject = payload.get("sub")
    if token_type != expected_type or not subject:
        return None
    return payload


def set_auth_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    settings = get_settings()
    cookie_args = {
        "httponly": True,
        "secure": settings.cookie_secure,
        "samesite": settings.cookie_samesite,
        "domain": settings.cookie_domain,
        "path": "/",
    }

    response.set_cookie(
        key=settings.access_cookie_name,
        value=access_token,
        max_age=settings.access_token_expire_minutes * 60,
        **cookie_args,
    )
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=refresh_token,
        max_age=settings.refresh_token_expire_days * 24 * 60 * 60,
        **cookie_args,
    )


def clear_auth_cookies(response: Response) -> None:
    settings = get_settings()
    cookie_args = {
        "httponly": True,
        "secure": settings.cookie_secure,
        "samesite": settings.cookie_samesite,
        "domain": settings.cookie_domain,
        "path": "/",
    }
    response.delete_cookie(settings.access_cookie_name, **cookie_args)
    response.delete_cookie(settings.refresh_cookie_name, **cookie_args)
