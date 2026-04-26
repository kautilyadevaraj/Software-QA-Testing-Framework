"""Scenario management endpoints — called by the Next.js frontend."""


import logging
import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from uuid6 import uuid7

from app.core.config import get_settings
from app.db.session import get_db
from app.dependencies.auth import get_current_user
from app.graph.scenario_graph import run_scenario_graph
from app.models.project import HighLevelScenario, Project
from app.models.scenario import RecordingSession
from app.models.user import User
from app.schemas.scenario import (
    ApproveScenariosRequest,
    ApproveScenariosResponse,
    GenerateScenariosRequest,
    GenerateScenariosResponse,
    HighLevelScenarioResponse,
    ManualScenarioRequest,
    RecordingSetupResponse,
    ScenarioListResponse,
    ScenarioUpdateRequest,
    TriggerScenarioResponse,
)
from app.services.project_service import get_project_or_404
from app.utils.rate_limiter import limiter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/projects", tags=["scenarios"])
settings = get_settings()


# ── Helpers ────────────────────────────────────────────────────────────────

def _scenario_response(row: HighLevelScenario, completed_by_name: str | None = None) -> HighLevelScenarioResponse:
    return HighLevelScenarioResponse(
        id=row.id,
        project_id=row.project_id,
        title=row.title,
        description=row.description,
        source=row.source,  # type: ignore[arg-type]
        status=row.status,  # type: ignore[arg-type]
        completed_by=row.completed_by,
        completed_by_name=completed_by_name,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _get_scenario_with_completed_by_name(
    db: Session,
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
) -> HighLevelScenarioResponse:
    result = db.execute(
        select(HighLevelScenario, User.email)
        .outerjoin(User, HighLevelScenario.completed_by == User.id)
        .where(
            HighLevelScenario.project_id == project_id,
            HighLevelScenario.id == scenario_id,
        )
    ).first()
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scenario not found")
    scenario, completed_by_name = result
    return _scenario_response(scenario, completed_by_name)


# ── Generate ───────────────────────────────────────────────────────────────

@router.post("/{project_id}/scenarios/generate", response_model=GenerateScenariosResponse)
@limiter.limit(settings.rate_limit_api)
def generate_scenarios(
    request: Request,
    project_id: uuid.UUID,
    payload: GenerateScenariosRequest = Body(default=GenerateScenariosRequest()),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> GenerateScenariosResponse:
    get_project_or_404(db, current_user.id, project_id)
    try:
        scenarios = run_scenario_graph(
            str(project_id),
            {
                "max_scenarios": payload.max_scenarios,
                "scenario_types": payload.scenario_types,
                "access_mode": payload.access_mode,
                "scenario_level": payload.scenario_level,
            },
            [
                {
                    "title": scenario.title,
                    "description": scenario.description,
                    "source": scenario.source,
                }
                for scenario in payload.existing_scenarios
            ],
        )
    except Exception as error:
        logger.exception("Scenario generation failed for project_id=%s: %s", project_id, error)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Scenario generation failed") from error
    return GenerateScenariosResponse(scenarios=scenarios)


# ── Approve ────────────────────────────────────────────────────────────────

@router.post("/{project_id}/scenarios/approve", response_model=ApproveScenariosResponse)
@limiter.limit(settings.rate_limit_api)
def approve_scenarios(
    request: Request,
    project_id: uuid.UUID,
    payload: ApproveScenariosRequest = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApproveScenariosResponse:
    get_project_or_404(db, current_user.id, project_id)
    rows = [
        HighLevelScenario(
            id=uuid7(),
            project_id=project_id,
            title=scenario.title,
            description=scenario.description,
            source=scenario.source,
        )
        for scenario in payload.scenarios
        if scenario.title.strip()
    ]
    if rows:
        db.add_all(rows)
        db.commit()
    return ApproveScenariosResponse(saved=len(rows))


# ── List ───────────────────────────────────────────────────────────────────

@router.get("/{project_id}/scenarios", response_model=ScenarioListResponse)
@limiter.limit(settings.rate_limit_api)
def list_scenarios(
    request: Request,
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ScenarioListResponse:
    get_project_or_404(db, current_user.id, project_id)
    rows = db.execute(
        select(HighLevelScenario, User.email)
        .outerjoin(User, HighLevelScenario.completed_by == User.id)
        .where(HighLevelScenario.project_id == project_id)
        .order_by(HighLevelScenario.id.asc())
    ).all()
    return ScenarioListResponse(scenarios=[_scenario_response(row, name) for row, name in rows])


# ── Create (manual) ────────────────────────────────────────────────────────

@router.post("/{project_id}/scenarios", response_model=HighLevelScenarioResponse)
@limiter.limit(settings.rate_limit_api)
def create_manual_scenario(
    request: Request,
    project_id: uuid.UUID,
    payload: ManualScenarioRequest = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> HighLevelScenarioResponse:
    get_project_or_404(db, current_user.id, project_id)
    if not payload.title:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Title is required")
    scenario = HighLevelScenario(
        id=uuid7(),
        project_id=project_id,
        title=payload.title,
        description=payload.description,
        source="manual",
    )
    db.add(scenario)
    db.commit()
    return _get_scenario_with_completed_by_name(db, project_id, scenario.id)


# ── Update ─────────────────────────────────────────────────────────────────

@router.patch("/{project_id}/scenarios/{scenario_id}", response_model=HighLevelScenarioResponse)
@limiter.limit(settings.rate_limit_api)
def update_scenario(
    request: Request,
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    payload: ScenarioUpdateRequest = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> HighLevelScenarioResponse:
    get_project_or_404(db, current_user.id, project_id)
    scenario = db.execute(
        select(HighLevelScenario).where(
            HighLevelScenario.project_id == project_id,
            HighLevelScenario.id == scenario_id,
        )
    ).scalar_one_or_none()
    if not scenario:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scenario not found")

    if payload.title is not None:
        if not payload.title:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Title is required")
        scenario.title = payload.title
    if payload.description is not None:
        scenario.description = payload.description
    if payload.status is not None:
        scenario.status = payload.status
        if payload.status == "completed":
            if payload.current_user_id is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="current_user_id is required when status is completed",
                )
            scenario.completed_by = payload.current_user_id
        else:
            scenario.completed_by = None

    db.add(scenario)
    db.commit()
    return _get_scenario_with_completed_by_name(db, project_id, scenario.id)


# ── Delete ─────────────────────────────────────────────────────────────────

@router.delete("/{project_id}/scenarios/{scenario_id}")
@limiter.limit(settings.rate_limit_api)
def delete_scenario(
    request: Request,
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, bool]:
    get_project_or_404(db, current_user.id, project_id)
    scenario = db.execute(
        select(HighLevelScenario).where(
            HighLevelScenario.project_id == project_id,
            HighLevelScenario.id == scenario_id,
        )
    ).scalar_one_or_none()
    if not scenario:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scenario not found")
    db.delete(scenario)
    db.commit()
    return {"deleted": True}


# ── Recording Setup (Web UI fetches daemon command) ────────────────────────

@router.get("/{project_id}/scenarios/recording-setup", response_model=RecordingSetupResponse)
@limiter.limit(settings.rate_limit_api)
def get_recording_setup(
    request: Request,
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RecordingSetupResponse:
    """
    Returns the one-time setup command the tester needs to run locally
    to start the recorder daemon for this project.
    """
    project = get_project_or_404(db, current_user.id, project_id)
    api_base = settings.PUBLIC_API_URL
    token = str(project.recorder_token)
    script_url = f"{api_base}/api/v1/recorder/{project_id}/script"
    # Two-step command: download the script then run it
    cmd = (
        f'curl -s -o recorder.py -H "X-Recorder-Token: {token}" {script_url} && python recorder.py'
    )
    return RecordingSetupResponse(setup_command=cmd, recorder_token=token)


# ── Trigger (Web UI → Daemon) ───────────────────────────────────────────────

@router.post("/{project_id}/scenarios/{scenario_id}/trigger", response_model=TriggerScenarioResponse)
@limiter.limit(settings.rate_limit_api)
def trigger_scenario_launch(
    request: Request,
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TriggerScenarioResponse:
    """
    Set active_launch_scenario_id on the Project.
    The Python daemon polls /pulse and picks this up within ~1 second,
    then atomically clears the field so each click fires exactly once.
    """
    get_project_or_404(db, current_user.id, project_id)

    # Verify scenario belongs to this project
    scenario = db.execute(
        select(HighLevelScenario).where(
            HighLevelScenario.project_id == project_id,
            HighLevelScenario.id == scenario_id,
        )
    ).scalar_one_or_none()
    if not scenario:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scenario not found")

    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    project.active_launch_scenario_id = scenario_id
    db.add(project)
    db.commit()

    return TriggerScenarioResponse(triggered=True, scenario_id=scenario_id)


# ── Recording Status (Web UI polls while recording) ────────────────────────

@router.get("/{project_id}/scenarios/{scenario_id}/recording-status")
@limiter.limit(settings.rate_limit_api)
def get_scenario_recording_status(
    request: Request,
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Returns the status of the most recent recording session for a scenario.
    The frontend polls this while a recording is active.
    Possible session_status values: 'none' | 'pending' | 'in_progress' | 'completed' | 'failed'
    """
    get_project_or_404(db, current_user.id, project_id)
    latest_session = db.execute(
        select(RecordingSession)
        .where(RecordingSession.scenario_id == scenario_id)
        .order_by(RecordingSession.created_at.desc())
    ).scalars().first()
    session_status = latest_session.status if latest_session else "none"
    return {"session_status": session_status}
