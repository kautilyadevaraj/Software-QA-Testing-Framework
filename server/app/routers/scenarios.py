"""Scenario management endpoints — called by the Next.js frontend."""


import json
import logging
import queue
import shutil
import threading
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from uuid6 import uuid7

from app.core.config import get_settings
from app.db.session import get_db
from app.dependencies.auth import get_current_user
from app.models.project import HighLevelScenario, Project
from app.models.scenario import DiscoveredRoute, RecordingFlow, RecordingSession, RouteVariant, ScenarioStep
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

def _latest_recording_info(db: Session, project_id: uuid.UUID, scenario_id: uuid.UUID) -> dict:
    session = db.execute(
        select(RecordingSession)
        .where(
            RecordingSession.project_id == project_id,
            RecordingSession.scenario_id == scenario_id,
        )
        .order_by(RecordingSession.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if session is None:
        return {
            "recording_status": None,
            "recording_step_count": 0,
            "recording_phase3_ready": None,
            "recording_quality_failure_reasons": [],
        }

    step_count = db.execute(
        select(func.count(ScenarioStep.id)).where(ScenarioStep.recording_session_id == session.id)
    ).scalar_one()
    flow = db.execute(
        select(RecordingFlow)
        .where(RecordingFlow.recording_id == session.id)
        .order_by(RecordingFlow.flow_index.desc())
        .limit(1)
    ).scalar_one_or_none()
    metadata = flow.metadata_json if flow and isinstance(flow.metadata_json, dict) else {}
    reasons = metadata.get("quality_failure_reasons") if isinstance(metadata, dict) else []

    return {
        "recording_status": session.status,
        "recording_step_count": int(step_count or 0),
        "recording_phase3_ready": bool(flow.phase3_ready) if flow else None,
        "recording_quality_failure_reasons": [str(reason) for reason in (reasons or [])],
    }


def _scenario_response(
    row: HighLevelScenario,
    completed_by_name: str | None = None,
    recording_info: dict | None = None,
) -> HighLevelScenarioResponse:
    recording_info = recording_info or {}
    return HighLevelScenarioResponse(
        id=row.id,
        project_id=row.project_id,
        title=row.title,
        description=row.description,
        source=row.source,  # type: ignore[arg-type]
        status=row.status,  # type: ignore[arg-type]
        completed_by=row.completed_by,
        completed_by_name=completed_by_name,
        recording_status=recording_info.get("recording_status"),
        recording_step_count=recording_info.get("recording_step_count") or 0,
        recording_phase3_ready=recording_info.get("recording_phase3_ready"),
        recording_quality_failure_reasons=recording_info.get("recording_quality_failure_reasons") or [],
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
    return _scenario_response(scenario, completed_by_name, _latest_recording_info(db, project_id, scenario.id))


def _recordings_base() -> Path:
    return Path(getattr(settings, "RECORDINGS_BASE_PATH", "recordings")).resolve()


def _remove_recording_path(path: str | None, recordings_base: Path) -> bool:
    if not path:
        return False

    target = Path(path)
    if not target.is_absolute():
        target = Path.cwd() / target
    try:
        resolved = target.resolve()
    except OSError:
        return False

    if recordings_base not in resolved.parents and resolved != recordings_base:
        return False

    if resolved.is_dir():
        shutil.rmtree(resolved, ignore_errors=True)
        return True
    if resolved.exists():
        resolved.unlink()
        return True
    return False


# ── Generate ───────────────────────────────────────────────────────────────

@router.post("/{project_id}/scenarios/generate")
@limiter.limit(settings.rate_limit_api)
def generate_scenarios(
    request: Request,
    project_id: uuid.UUID,
    payload: GenerateScenariosRequest = Body(default=GenerateScenariosRequest()),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    get_project_or_404(db, current_user.id, project_id)

    q = queue.Queue()

    def progress_callback(msg: str):
        q.put({"type": "progress", "message": msg})

    def run_generation():
        try:
            from app.graph.scenario_graph import run_scenario_graph
            scenarios = run_scenario_graph(
                str(project_id),
                {
                    "max_scenarios": payload.max_scenarios,
                    "scenario_types": payload.scenario_types,
                    "access_mode": payload.access_mode,
                    "scenario_level": payload.scenario_level,
                    "progress_callback": progress_callback,
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
            q.put({"type": "complete", "scenarios": scenarios})
        except Exception as error:
            logger.exception("Scenario generation failed for project_id=%s: %s", project_id, error)
            q.put({"type": "error", "message": str(error)})

    threading.Thread(target=run_generation, daemon=True).start()

    async def event_generator() -> AsyncGenerator[str, None]:
        while True:
            try:
                # Use a small timeout so we don't block the async generator indefinitely
                item = q.get(timeout=0.1)
                yield json.dumps(item) + "\n"
                if item["type"] in ("complete", "error"):
                    break
            except queue.Empty:
                # Check if client disconnected
                if await request.is_disconnected():
                    break

    return StreamingResponse(event_generator(), media_type="application/x-ndjson")


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
    return ScenarioListResponse(
        scenarios=[
            _scenario_response(row, name, _latest_recording_info(db, project_id, row.id))
            for row, name in rows
        ]
    )


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
    clear_scenario_recording(request, project_id, scenario_id, db, current_user)
    scenario = db.execute(
        select(HighLevelScenario).where(
            HighLevelScenario.project_id == project_id,
            HighLevelScenario.id == scenario_id,
        )
    ).scalar_one_or_none()
    if not scenario:
        return {"deleted": True}
    db.delete(scenario)
    db.commit()
    return {"deleted": True}


@router.delete("/{project_id}/scenarios/{scenario_id}/recording")
@limiter.limit(settings.rate_limit_api)
def clear_scenario_recording(
    request: Request,
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, int | bool]:
    get_project_or_404(db, current_user.id, project_id)
    scenario = db.execute(
        select(HighLevelScenario).where(
            HighLevelScenario.project_id == project_id,
            HighLevelScenario.id == scenario_id,
        )
    ).scalar_one_or_none()
    if not scenario:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scenario not found")

    sessions = db.execute(
        select(RecordingSession).where(
            RecordingSession.project_id == project_id,
            RecordingSession.scenario_id == scenario_id,
        )
    ).scalars().all()
    session_ids = [session.id for session in sessions]

    steps: list[ScenarioStep] = []
    variants: list[RouteVariant] = []
    if session_ids:
        steps = db.execute(
            select(ScenarioStep).where(ScenarioStep.recording_session_id.in_(session_ids))
        ).scalars().all()
        variants = db.execute(
            select(RouteVariant).where(RouteVariant.recording_session_id.in_(session_ids))
        ).scalars().all()

    route_ids = {variant.route_id for variant in variants}
    recordings_base = _recordings_base()
    files_deleted = 0

    for step in steps:
        if _remove_recording_path(step.screenshot_path, recordings_base):
            files_deleted += 1

    for variant in variants:
        if _remove_recording_path(variant.html_path, recordings_base):
            files_deleted += 1
        if _remove_recording_path(variant.screenshot_path, recordings_base):
            files_deleted += 1

    for session_id in session_ids:
        session_dir = recordings_base / str(project_id) / str(session_id)
        if _remove_recording_path(str(session_dir), recordings_base):
            files_deleted += 1

    for step in steps:
        db.delete(step)
    for variant in variants:
        db.delete(variant)
    for session in sessions:
        db.delete(session)

    project = db.get(Project, project_id)
    if project and project.active_launch_scenario_id == scenario_id:
        project.active_launch_scenario_id = None

    scenario.status = "pending"
    scenario.completed_by = None

    db.flush()

    routes_deleted = 0
    for route_id in route_ids:
        remaining_variants = db.execute(
            select(func.count(RouteVariant.id)).where(RouteVariant.route_id == route_id)
        ).scalar_one()
        route = db.get(DiscoveredRoute, route_id)
        if not route:
            continue
        if remaining_variants == 0:
            if _remove_recording_path(route.html_path, recordings_base):
                files_deleted += 1
            if _remove_recording_path(route.screenshot_path, recordings_base):
                files_deleted += 1
            db.delete(route)
            routes_deleted += 1
        else:
            route.html_path = None
            route.screenshot_path = None

    db.commit()

    return {
        "cleared": True,
        "sessions_deleted": len(sessions),
        "steps_deleted": len(steps),
        "route_variants_deleted": len(variants),
        "routes_deleted": routes_deleted,
        "files_deleted": files_deleted,
    }


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
        f'curl.exe -s -o recorder.py -H "X-Recorder-Token: {token}" "{script_url}"; python recorder.py'
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
    info = _latest_recording_info(db, project_id, scenario_id)
    return {
        "session_status": session_status,
        "phase3_ready": info["recording_phase3_ready"],
        "step_count": info["recording_step_count"],
        "quality_failure_reasons": info["recording_quality_failure_reasons"],
    }


@router.post("/{project_id}/scenarios/{scenario_id}/stop-recording")
@limiter.limit(settings.rate_limit_api)
def stop_scenario_recording(
    request: Request,
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Marks the active recording session for a scenario as completed.
    The daemon polls the session status and will automatically close the browser
    when it sees the completed status.
    """
    get_project_or_404(db, current_user.id, project_id)
    
    session = db.execute(
        select(RecordingSession).where(
            RecordingSession.project_id == project_id,
            RecordingSession.scenario_id == scenario_id,
            RecordingSession.status.in_(["pending", "in_progress"])
        )
    ).scalar_one_or_none()
    
    if session:
        from sqlalchemy.sql import func
        session.status = "completed"
        session.completed_at = func.now()
        db.commit()
        
    return {"status": "stopped"}
