"""DB registry for Phase 3 generated evidence artifacts."""
from __future__ import annotations

import mimetypes
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.phase3 import Phase3Artifact

_MIME_BY_TYPE = {
    "SCRIPT": "text/typescript",
    "TRACE": "application/zip",
    "VIDEO": "video/webm",
    "SCREENSHOT": "image/png",
    "XRAY_CSV": "text/csv",
    "MANIFEST": "application/json",
    "REPORT": "application/json",
}


def _uuid_or_none(value: str | uuid.UUID | None) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _file_size(path: Path) -> int | None:
    try:
        return path.stat().st_size if path.exists() else None
    except OSError:
        return None


def register_artifact(
    *,
    project_id: str | uuid.UUID,
    run_id: str | uuid.UUID,
    artifact_type: str,
    path: str | Path,
    test_id: str | uuid.UUID | None = None,
    mime_type: str | None = None,
) -> str | None:
    """Upsert an ACTIVE artifact row and return artifact_id."""
    project_uuid = _uuid_or_none(project_id)
    run_uuid = _uuid_or_none(run_id)
    test_uuid = _uuid_or_none(test_id)
    if not project_uuid or not run_uuid:
        return None

    artifact_type = artifact_type.upper()
    artifact_path = Path(path)
    resolved_path = str(artifact_path)
    filename = artifact_path.name
    guessed_mime = mime_type or _MIME_BY_TYPE.get(artifact_type) or mimetypes.guess_type(filename)[0]

    with SessionLocal() as db:
        existing = db.execute(
            select(Phase3Artifact).where(
                Phase3Artifact.run_id == run_uuid,
                Phase3Artifact.test_id.is_(test_uuid) if test_uuid is None else Phase3Artifact.test_id == test_uuid,
                Phase3Artifact.artifact_type == artifact_type,
                Phase3Artifact.path == resolved_path,
            )
        ).scalar_one_or_none()
        if existing:
            existing.status = "ACTIVE"
            existing.filename = filename
            existing.mime_type = guessed_mime
            existing.size_bytes = _file_size(artifact_path)
            artifact = existing
        else:
            artifact = Phase3Artifact(
                artifact_id=uuid.uuid4(),
                project_id=project_uuid,
                run_id=run_uuid,
                test_id=test_uuid,
                artifact_type=artifact_type,
                path=resolved_path,
                filename=filename,
                mime_type=guessed_mime,
                size_bytes=_file_size(artifact_path),
                status="ACTIVE",
            )
            db.add(artifact)
        db.commit()
        return str(artifact.artifact_id)


def register_many(entries: list[dict[str, Any]]) -> None:
    for entry in entries:
        register_artifact(**entry)
