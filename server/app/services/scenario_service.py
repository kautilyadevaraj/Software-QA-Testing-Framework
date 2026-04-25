"""Business logic for Phase 2 scenario management."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.project import Project
from app.models.scenario import (
    RecordingSession,
    TestScenario,
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


def _scenario_to_response(scenario: TestScenario, session_status: str | None = None) -> ScenarioResponse:
    return ScenarioResponse(
        id=scenario.id,
        project_id=scenario.project_id,
        title=scenario.title,
        description=scenario.description,
        source=scenario.source,
        status=scenario.status,
        completed_by=scenario.completed_by,
        created_at=scenario.created_at,
        updated_at=scenario.updated_at,
        recording_status=session_status,
    )


def list_scenarios(db: Session, project_id: uuid.UUID) -> ScenarioListResponse:
    """Return all scenarios for a project, enriched with recording session status."""
    scenarios = (
        db.execute(
            select(TestScenario)
            .where(TestScenario.project_id == project_id)
            .order_by(TestScenario.created_at)
        )
        .scalars()
        .all()
    )

    # Get the latest recording session status per scenario in one query
    latest_sessions: dict[uuid.UUID, str] = {}
    if scenarios:
        scenario_ids = [s.id for s in scenarios]
        # Subquery: rank sessions per scenario by created_at desc
        rows = db.execute(
            select(
                RecordingSession.scenario_id,
                RecordingSession.status,
            )
            .where(RecordingSession.scenario_id.in_(scenario_ids))
            .order_by(
                RecordingSession.scenario_id,
                RecordingSession.created_at.desc(),
            )
        ).all()
        seen: set[uuid.UUID] = set()
        for row in rows:
            if row.scenario_id not in seen:
                latest_sessions[row.scenario_id] = row.status
                seen.add(row.scenario_id)

    items = [
        _scenario_to_response(s, latest_sessions.get(s.id)) for s in scenarios
    ]
    return ScenarioListResponse(items=items, total=len(items))


def create_scenario(
    db: Session, project_id: uuid.UUID, payload: ScenarioCreate, user_id: uuid.UUID
) -> ScenarioResponse:
    scenario = TestScenario(
        project_id=project_id,
        title=payload.title,
        description=payload.description,
        source=payload.source,
        status="pending",
    )
    db.add(scenario)
    db.commit()
    db.refresh(scenario)
    return _scenario_to_response(scenario)


def update_scenario(
    db: Session,
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    payload: ScenarioUpdate,
) -> ScenarioResponse:
    scenario = db.execute(
        select(TestScenario).where(
            TestScenario.id == scenario_id,
            TestScenario.project_id == project_id,
        )
    ).scalar_one_or_none()

    if scenario is None:
        raise ValueError(f"Scenario {scenario_id} not found in project {project_id}")

    if payload.title is not None:
        scenario.title = payload.title
    if payload.description is not None:
        scenario.description = payload.description

    db.commit()
    db.refresh(scenario)
    return _scenario_to_response(scenario)


def delete_scenario(
    db: Session, project_id: uuid.UUID, scenario_id: uuid.UUID
) -> None:
    scenario = db.execute(
        select(TestScenario).where(
            TestScenario.id == scenario_id,
            TestScenario.project_id == project_id,
        )
    ).scalar_one_or_none()

    if scenario is None:
        raise ValueError(f"Scenario {scenario_id} not found")

    db.delete(scenario)
    db.commit()


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
            select(TestScenario).where(TestScenario.project_id == project_id)
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
        select(func.count()).where(TestScenario.project_id == project_id)
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
        select(RecordingSession, TestScenario.title)
        .join(TestScenario, RecordingSession.scenario_id == TestScenario.id)
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