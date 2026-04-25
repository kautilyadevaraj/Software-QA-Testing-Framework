"""
Recorder API — called exclusively by the Python recorder script running on the
tester's local machine. Authentication uses a project-scoped recorder_token
passed in the X-Recorder-Token header.

These endpoints are NOT protected by JWT. The recorder_token acts as the secret.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Response
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session
from fastapi import Depends

from app.db.session import get_db
from app.schemas.scenario import (
    RecorderProjectInfo,
    RecorderRouteResponse,
    RecorderRouteUpsert,
    RecorderSessionCreate,
    RecorderSessionResponse,
    RecorderStepCreate,
    RecorderStepResponse,
)
from app.services import recorder_service
from app.core.config import settings

router = APIRouter(prefix="/recorder", tags=["recorder"])


def _get_project_by_token(
    project_id: uuid.UUID,
    x_recorder_token: str = Header(...),
    db: Session = Depends(get_db),
):
    try:
        return recorder_service.validate_recorder_token(db, project_id, x_recorder_token)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Invalid recorder token")
    except ValueError:
        raise HTTPException(status_code=404, detail="Project not found")


# ── Script download ─────────────────────────────────────────────────────────

@router.get("/{project_id}/script", response_class=PlainTextResponse)
def download_recorder_script(
    project_id: uuid.UUID,
    x_recorder_token: str = Header(...),
    db: Session = Depends(get_db),
) -> Response:
    project = _get_project_by_token(project_id, x_recorder_token, db)
    script = recorder_service.get_recorder_script(
        project_id=project.id,
        recorder_token=str(project.recorder_token),
        server_url=settings.PUBLIC_API_URL,
    )
    return Response(
        content=script,
        media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="recorder.py"'},
    )


# ── Project info ────────────────────────────────────────────────────────────

@router.get("/{project_id}/info", response_model=RecorderProjectInfo)
def get_project_info(
    project_id: uuid.UUID,
    x_recorder_token: str = Header(...),
    db: Session = Depends(get_db),
) -> RecorderProjectInfo:
    project = _get_project_by_token(project_id, x_recorder_token, db)
    return recorder_service.get_project_info(db, project)


# ── Sessions ────────────────────────────────────────────────────────────────

@router.post("/{project_id}/sessions", response_model=RecorderSessionResponse)
def create_session(
    project_id: uuid.UUID,
    payload: RecorderSessionCreate,
    x_recorder_token: str = Header(...),
    db: Session = Depends(get_db),
) -> RecorderSessionResponse:
    project = _get_project_by_token(project_id, x_recorder_token, db)
    try:
        return recorder_service.create_session(db, project, payload.scenario_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/{project_id}/sessions/{session_id}/start", response_model=RecorderSessionResponse)
def start_session(
    project_id: uuid.UUID,
    session_id: uuid.UUID,
    x_recorder_token: str = Header(...),
    db: Session = Depends(get_db),
) -> RecorderSessionResponse:
    project = _get_project_by_token(project_id, x_recorder_token, db)
    try:
        return recorder_service.start_session(db, project, session_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("/{project_id}/sessions/{session_id}/complete", response_model=RecorderSessionResponse)
def complete_session(
    project_id: uuid.UUID,
    session_id: uuid.UUID,
    x_recorder_token: str = Header(...),
    db: Session = Depends(get_db),
) -> RecorderSessionResponse:
    project = _get_project_by_token(project_id, x_recorder_token, db)
    try:
        return recorder_service.complete_session(db, project, session_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("/{project_id}/sessions/{session_id}/fail", response_model=RecorderSessionResponse)
def fail_session(
    project_id: uuid.UUID,
    session_id: uuid.UUID,
    x_recorder_token: str = Header(...),
    db: Session = Depends(get_db),
) -> RecorderSessionResponse:
    project = _get_project_by_token(project_id, x_recorder_token, db)
    try:
        return recorder_service.fail_session(db, project, session_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── Routes ──────────────────────────────────────────────────────────────────

@router.post("/{project_id}/routes", response_model=RecorderRouteResponse)
def upsert_route(
    project_id: uuid.UUID,
    payload: RecorderRouteUpsert,
    x_recorder_token: str = Header(...),
    db: Session = Depends(get_db),
) -> RecorderRouteResponse:
    project = _get_project_by_token(project_id, x_recorder_token, db)
    return recorder_service.upsert_route(db, project, payload)


# ── Steps ───────────────────────────────────────────────────────────────────

@router.post(
    "/{project_id}/sessions/{session_id}/steps",
    response_model=RecorderStepResponse,
    status_code=201,
)
def append_step(
    project_id: uuid.UUID,
    session_id: uuid.UUID,
    payload: RecorderStepCreate,
    x_recorder_token: str = Header(...),
    db: Session = Depends(get_db),
) -> RecorderStepResponse:
    project = _get_project_by_token(project_id, x_recorder_token, db)
    try:
        return recorder_service.append_step(db, project, session_id, payload)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))