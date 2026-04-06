from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import create_access_token, create_refresh_token, hash_password, verify_password
from app.models.user import User


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.execute(select(User).where(User.email == email.lower())).scalar_one_or_none()


def create_user(db: Session, email: str, password: str) -> User:
    user = User(email=email.lower(), password_hash=hash_password(password), role="user")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate_user(db: Session, email: str, password: str) -> User | None:
    user = get_user_by_email(db, email)
    if not user:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def issue_tokens_for_user(user_id: uuid.UUID) -> tuple[str, str]:
    subject = str(user_id)
    access_token = create_access_token(subject)
    refresh_token = create_refresh_token(subject)
    return access_token, refresh_token
