from __future__ import annotations

import uuid

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import decode_token
from app.db.session import get_db
from app.models.user import User


def _extract_bearer_token(request: Request) -> str | None:
    authorization = request.headers.get("Authorization")
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    settings = get_settings()
    token = request.cookies.get(settings.access_cookie_name) or _extract_bearer_token(request)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

    payload = decode_token(token, expected_type="access")
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    try:
        user_id = uuid.UUID(str(payload["sub"]))
    except (ValueError, KeyError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication token") from None

    user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User no longer exists")

    return user
