from __future__ import annotations

import base64
import csv
import hashlib
import uuid
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.project import CredentialProfile, Project, ProjectFile


def _fernet() -> Fernet:
    digest = hashlib.sha256(settings.jwt_secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str) -> str:
    if not value:
        return ""
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(value: str) -> str:
    if not value:
        return ""
    return _fernet().decrypt(value.encode("utf-8")).decode("utf-8")


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


def read_credential_rows(file_path: str | Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with open(file_path, newline="", encoding="utf-8-sig") as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            username = _row_value(row, "username", "user", "email", "email address")
            if not username:
                continue
            rows.append(
                {
                    "username": username,
                    "password": _row_value(row, "password", "pass", "secret"),
                    "role": _row_value(row, "role", default="user"),
                    "auth_type": _row_value(row, "authtype", "auth_type", "auth type"),
                    "endpoint": _row_value(row, "api endpoint", "endpoint", "url", "base_url"),
                }
            )
    return rows


def sync_profiles_from_credentials_file(
    db: Session,
    project: Project,
    credentials_file: ProjectFile,
) -> int:
    """Upsert credential profiles from the project's uploaded credentials CSV."""
    rows = read_credential_rows(credentials_file.absolute_path)
    synced = 0

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
                    endpoint=row.get("endpoint", ""),
                    is_verified=False,
                )
            )
        synced += 1

    db.commit()
    return synced


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
