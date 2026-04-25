"""Scenario management endpoints — called by the Next.js frontend."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status, Response
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.dependencies.auth import get_current_user
from app.models.user import User
from app.schemas.scenario import (
    LockScenariosResponse,
    Phase2StatusResponse,
    RecordingSessionListResponse,
    RecordingSetupResponse,
    ScenarioCreate,
    ScenarioListResponse,
    ScenarioResponse,
    ScenarioUpdate,
)
from app.services import scenario_service, recorder_service
from app.core.config import settings

router = APIRouter(prefix="/projects/{project_id}/scenarios", tags=["scenarios"])


def _require_project_member(
    project_id: uuid.UUID,
    db: Session,
    current_user: User,
) -> None:
    """
    Raise 403 if the current user is not a member of the project.
    Re-use your existing project membership check here.
    """
    from app.services.project_service import assert_project_member  # adjust import
    assert_project_member(db, project_id, current_user.id)


# ── Scenario CRUD ──────────────────────────────────────────────────────────

@router.get("", response_model=ScenarioListResponse)
def list_scenarios(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ScenarioListResponse:
    _require_project_member(project_id, db, current_user)
    return scenario_service.list_scenarios(db, project_id)


@router.post("", response_model=ScenarioResponse, status_code=status.HTTP_201_CREATED)
def create_scenario(
    project_id: uuid.UUID,
    payload: ScenarioCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ScenarioResponse:
    _require_project_member(project_id, db, current_user)
    return scenario_service.create_scenario(db, project_id, payload, current_user.id)


@router.patch("/{scenario_id}", response_model=ScenarioResponse)
def update_scenario(
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    payload: ScenarioUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ScenarioResponse:
    _require_project_member(project_id, db, current_user)
    try:
        return scenario_service.update_scenario(db, project_id, scenario_id, payload)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/{scenario_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None, response_class=Response)
def delete_scenario(
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    _require_project_member(project_id, db, current_user)
    try:
        scenario_service.delete_scenario(db, project_id, scenario_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── Lock ───────────────────────────────────────────────────────────────────

@router.post("/lock", response_model=LockScenariosResponse)
def lock_scenarios(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> LockScenariosResponse:
    _require_project_member(project_id, db, current_user)
    try:
        sessions_created, already_locked = scenario_service.lock_scenarios(db, project_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return LockScenariosResponse(locked=True, sessions_created=sessions_created)


# ── Phase 2 status ─────────────────────────────────────────────────────────

@router.get("/phase2-status", response_model=Phase2StatusResponse)
def get_phase2_status(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Phase2StatusResponse:
    _require_project_member(project_id, db, current_user)
    try:
        return scenario_service.get_phase2_status(db, project_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── Recording sessions (frontend read) ─────────────────────────────────────

@router.get("/recording-sessions", response_model=RecordingSessionListResponse)
def list_recording_sessions(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RecordingSessionListResponse:
    _require_project_member(project_id, db, current_user)
    return scenario_service.list_recording_sessions(db, project_id)


# ── Recorder setup command ──────────────────────────────────────────────────

@router.get("/recording-setup", response_model=RecordingSetupResponse)
def get_recording_setup(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RecordingSetupResponse:
    _require_project_member(project_id, db, current_user)
    from app.models.project import Project
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    token = str(project.recorder_token)
    server_url = settings.PUBLIC_API_URL  # e.g. https://your-vm.com

    # The tester runs this command on their local machine
    setup_command = (
        f'curl -o recorder.py "{server_url}/api/v1/recorder/{project_id}/script" '
        f'-H "X-Recorder-Token: {token}" && '
        f"pip install playwright && "
        f"playwright install chromium && "
        f"python recorder.py"
    )

    return RecordingSetupResponse(setup_command=setup_command, recorder_token=token)