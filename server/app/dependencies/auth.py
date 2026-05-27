from __future__ import annotations

import uuid

from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import decode_token, set_auth_cookies
from app.db.session import get_db
from app.models.user import User
from app.services.auth_service import issue_tokens_for_user


def _extract_bearer_token(request: Request) -> str | None:
    authorization = request.headers.get("Authorization")
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def _user_id_from_payload(payload: dict[str, object], error_message: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(payload["sub"]))
    except (ValueError, KeyError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=error_message) from None


def _load_user(db: Session, user_id: uuid.UUID) -> User:
    user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User no longer exists")
    return user


def get_current_user(request: Request, response: Response, db: Session = Depends(get_db)) -> User:
    settings = get_settings()
    token = request.cookies.get(settings.access_cookie_name) or _extract_bearer_token(request)
    if token:
        payload = decode_token(token, expected_type="access")
        if payload:
            return _load_user(db, _user_id_from_payload(payload, "Invalid authentication token"))

    refresh_token = request.cookies.get(settings.refresh_cookie_name)
    if not refresh_token:
        detail = "Invalid or expired token" if token else "Authentication required"
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)

    refresh_payload = decode_token(refresh_token, expected_type="refresh")
    if not refresh_payload:
        detail = "Invalid or expired token" if token else "Authentication required"
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)

    user_id = _user_id_from_payload(refresh_payload, "Invalid refresh token")
    user = _load_user(db, user_id)
    access_token, refresh_token_value = issue_tokens_for_user(user.id)
    set_auth_cookies(response, access_token, refresh_token_value)

    return user
