from __future__ import annotations

import base64
import csv
import hashlib
import uuid
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.project import CredentialProfile, Project, ProjectFile


def _fernet_for(key_material: str) -> Fernet:
    digest = hashlib.sha256(key_material.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _fernet() -> Fernet:
    return _fernet_for(settings.credential_encryption_key or settings.jwt_secret_key)


def encrypt_secret(value: str) -> str:
    if not value:
        return ""
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(value: str) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        if not settings.credential_encryption_key:
            raise
        return _fernet_for(settings.jwt_secret_key).decrypt(value.encode("utf-8")).decode("utf-8")


def _clean(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _row_value(row: dict[str, Any], *names: str, default: str = "") -> str:
    normalized = {str(k).strip().lower(): v for k, v in row.items()}
    for name in names:
        key = name.strip().lower()
        if key in normalized:
            return _clean(normalized[key], default)
    return default


_AUTH_STRATEGIES = {
    "inline_login",
    "storage_state",
    "api_key",
    "manual_session",
    "custom_script",
}


def normalize_auth_strategy(value: str | None, *, auth_type: str | None = None) -> str:
    raw = _clean(value or "").lower().replace("-", "_").replace(" ", "_")
    type_raw = _clean(auth_type or "").lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "inline": "inline_login",
        "login": "inline_login",
        "form_login": "inline_login",
        "browser_login": "inline_login",
        "storage": "storage_state",
        "storage_state_file": "storage_state",
        "session": "manual_session",
        "manual": "manual_session",
        "custom": "custom_script",
        "script": "custom_script",
    }
    candidate = aliases.get(raw, raw)
    if candidate in _AUTH_STRATEGIES:
        return candidate
    type_candidate = aliases.get(type_raw, type_raw)
    if type_candidate in _AUTH_STRATEGIES:
        return type_candidate
    return "inline_login"


def read_credential_rows(file_path: str | Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with open(file_path, newline="", encoding="utf-8-sig") as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            username = _row_value(row, "username", "user", "email", "email address")
            if not username:
                continue
            auth_type = _row_value(row, "authtype", "auth_type", "auth type")
            auth_strategy = _row_value(row, "auth_strategy", "auth strategy", "strategy")
            rows.append({
                "username": username,
                "password": _row_value(row, "password", "pass", "secret"),
                "role": _row_value(row, "role", default="user"),
                "auth_type": auth_type,
                "auth_strategy": normalize_auth_strategy(auth_strategy, auth_type=auth_type),
                "auth_script": _row_value(row, "auth_script", "auth script", "auth setup script"),
                "endpoint": _row_value(row, "api endpoint", "endpoint", "url", "base_url"),
            })
    return rows


def sync_profiles_from_credentials_file(
    db: Session,
    project: Project,
    credentials_file: ProjectFile,
) -> int:
    """Replace this project's credential profiles from the uploaded CSV."""
    rows = read_credential_rows(credentials_file.absolute_path)
    synced = 0
    incoming_keys = {
        (row["username"], row.get("role") or "user")
        for row in rows
    }

    for row in rows:
        username = row["username"]
        role = row.get("role") or "user"
        existing = db.execute(
            select(CredentialProfile).where(
                CredentialProfile.project_id == project.id,
                CredentialProfile.username == username,
                CredentialProfile.role == role,
            )
        ).scalar_one_or_none()

        password_ciphertext = encrypt_secret(row.get("password", ""))
        if existing:
            existing.password_ciphertext = password_ciphertext
            existing.auth_type = row.get("auth_type", "")
            existing.auth_strategy = normalize_auth_strategy(
                row.get("auth_strategy"),
                auth_type=row.get("auth_type"),
            )
            existing.auth_script = row.get("auth_script") or None
            existing.endpoint = row.get("endpoint", "")
            existing.source_file_id = credentials_file.id
        else:
            db.add(
                CredentialProfile(
                    id=uuid.uuid4(),
                    project_id=project.id,
                    source_file_id=credentials_file.id,
                    username=username,
                    password_ciphertext=password_ciphertext,
                    role=role,
                    auth_type=row.get("auth_type", ""),
                    auth_strategy=normalize_auth_strategy(
                        row.get("auth_strategy"),
                        auth_type=row.get("auth_type"),
                    ),
                    auth_script=row.get("auth_script") or None,
                    endpoint=row.get("endpoint", ""),
                    is_verified=False,
                )
            )
        synced += 1

    existing_profiles = db.execute(
        select(CredentialProfile).where(CredentialProfile.project_id == project.id)
    ).scalars().all()
    for profile in existing_profiles:
        key = (profile.username, profile.role or "user")
        if key not in incoming_keys:
            db.delete(profile)

    db.commit()
    return synced


def delete_profiles_for_credentials_file(
    db: Session,
    project_id: uuid.UUID,
    file_id: uuid.UUID,
) -> int:
    profiles = db.execute(
        select(CredentialProfile).where(
            CredentialProfile.project_id == project_id,
            CredentialProfile.source_file_id == file_id,
        )
    ).scalars().all()
    deleted = len(profiles)
    for profile in profiles:
        db.delete(profile)
    return deleted


def list_project_profiles(db: Session, project_id: uuid.UUID) -> list[CredentialProfile]:
    return list(
        db.execute(
            select(CredentialProfile)
            .where(CredentialProfile.project_id == project_id)
            .order_by(CredentialProfile.role.asc(), CredentialProfile.username.asc())
        )
        .scalars()
        .all()
    )


def get_profile_password(profile: CredentialProfile) -> str:
    return decrypt_secret(profile.password_ciphertext)
