"""Business logic for Phase 2 scenario management."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.project import Project
from app.models.scenario import (
    RecordingSession,
    HighLevelScenario,
)
from app.schemas.scenario import (
    Phase2StatusResponse,
    RecordingSessionListResponse,
    RecordingSessionResponse,
    ScenarioCreate,
    ScenarioListResponse,
    ScenarioResponse,
    ScenarioUpdate,
)


def lock_scenarios(db: Session, project_id: uuid.UUID) -> tuple[int, bool]:
    """
    Lock the scenario list for a project:
    1. Sets project.phase_2_locked = True
    2. Creates one RecordingSession per scenario that doesn't already have one

    Returns (sessions_created_count, already_locked)
    """
    project = db.get(Project, project_id)
    if project is None:
        raise ValueError(f"Project {project_id} not found")

    if project.phase_2_locked:
        return 0, True

    scenarios = (
        db.execute(
            select(HighLevelScenario).where(HighLevelScenario.project_id == project_id)
        )
        .scalars()
        .all()
    )

    if not scenarios:
        raise ValueError("Cannot lock — no scenarios exist for this project")

    # Find scenarios that already have a recording session
    existing_session_scenario_ids: set[uuid.UUID] = {
        row.scenario_id
        for row in db.execute(
            select(RecordingSession.scenario_id).where(
                RecordingSession.project_id == project_id
            )
        ).all()
    }

    sessions_created = 0
    for scenario in scenarios:
        if scenario.id not in existing_session_scenario_ids:
            session = RecordingSession(
                project_id=project_id,
                scenario_id=scenario.id,
                status="pending",
            )
            db.add(session)
            sessions_created += 1

    project.phase_2_locked = True
    db.commit()
    return sessions_created, False


def get_phase2_status(db: Session, project_id: uuid.UUID) -> Phase2StatusResponse:
    project = db.get(Project, project_id)
    if project is None:
        raise ValueError(f"Project {project_id} not found")

    total = db.scalar(
        select(func.count()).where(HighLevelScenario.project_id == project_id)
    ) or 0

    recorded = db.scalar(
        select(func.count()).where(
            RecordingSession.project_id == project_id,
            RecordingSession.status == "completed",
        )
    ) or 0

    return Phase2StatusResponse(
        phase_2_locked=project.phase_2_locked,
        total_scenarios=total,
        recorded_scenarios=recorded,
        all_recorded=(total > 0 and recorded >= total),
    )


def list_recording_sessions(
    db: Session, project_id: uuid.UUID
) -> RecordingSessionListResponse:
    rows = db.execute(
        select(RecordingSession, HighLevelScenario.title)
        .join(HighLevelScenario, RecordingSession.scenario_id == HighLevelScenario.id)
        .where(RecordingSession.project_id == project_id)
        .order_by(RecordingSession.created_at)
    ).all()

    items = []
    for session, scenario_title in rows:
        step_count = db.scalar(
            select(func.count()).where(
                RecordingSession.id == session.id,
            ).select_from(
                __import__("app.models.scenario", fromlist=["ScenarioStep"]).ScenarioStep
            ).where(
                __import__("app.models.scenario", fromlist=["ScenarioStep"]).ScenarioStep.recording_session_id == session.id
            )
        ) or 0

        items.append(
            RecordingSessionResponse(
                id=session.id,
                project_id=session.project_id,
                scenario_id=session.scenario_id,
                scenario_title=scenario_title,
                status=session.status,
                started_at=session.started_at,
                completed_at=session.completed_at,
                created_at=session.created_at,
                step_count=step_count,
            )
        )

    return RecordingSessionListResponse(items=items)