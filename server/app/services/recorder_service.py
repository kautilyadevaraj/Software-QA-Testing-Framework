"""Business logic for the recorder API — called by the Python recorder script."""

from __future__ import annotations

import base64
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.project import Project
from app.models.scenario import (
    DiscoveredRoute,
    RecordingSession,
    RouteVariant,
    ScenarioStep,
    TestScenario,
)
from app.schemas.scenario import (
    RecorderProjectInfo,
    RecorderRouteResponse,
    RecorderRouteUpsert,
    RecorderScenarioInfo,
    RecorderSessionResponse,
    RecorderStepCreate,
    RecorderStepResponse,
)


# ── Helpers ────────────────────────────────────────────────────────────────

def _recordings_base() -> Path:
    base = Path(getattr(settings, "RECORDINGS_BASE_PATH", "uploads/recordings"))
    base.mkdir(parents=True, exist_ok=True)
    return base


def _save_file(data_b64: str, folder: Path, filename: str) -> str:
    """Decode base64 string and write to disk. Returns the relative path."""
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / filename
    dest.write_bytes(base64.b64decode(data_b64))
    return str(dest)


def _url_path(url: str) -> str:
    """Extract the path component of a URL, normalising trailing slashes."""
    parsed = urlparse(url)
    path = parsed.path.rstrip("/") or "/"
    return path


# ── Auth helper ────────────────────────────────────────────────────────────

def validate_recorder_token(db: Session, project_id: uuid.UUID, token: str) -> Project:
    """Raise ValueError if token doesn't match. Returns the project."""
    project = db.get(Project, project_id)
    if project is None:
        raise ValueError("Project not found")
    if str(project.recorder_token) != token:
        raise PermissionError("Invalid recorder token")
    return project


# ── Endpoints ──────────────────────────────────────────────────────────────

def get_project_info(db: Session, project: Project) -> RecorderProjectInfo:
    scenarios = (
        db.execute(
            select(TestScenario).where(
                TestScenario.project_id == project.id
            ).order_by(TestScenario.created_at)
        )
        .scalars()
        .all()
    )
    return RecorderProjectInfo(
        project_id=project.id,
        project_name=project.name,
        project_url=project.url or "",
        scenarios=[
            RecorderScenarioInfo(
                id=s.id,
                title=s.title,
                description=s.description,
                status=s.status,
            )
            for s in scenarios
        ],
    )


def create_session(
    db: Session, project: Project, scenario_id: uuid.UUID
) -> RecorderSessionResponse:
    # Check scenario belongs to project
    scenario = db.execute(
        select(TestScenario).where(
            TestScenario.id == scenario_id,
            TestScenario.project_id == project.id,
        )
    ).scalar_one_or_none()
    if scenario is None:
        raise ValueError("Scenario not found in this project")

    # Reuse existing pending/failed session or create new
    existing = db.execute(
        select(RecordingSession).where(
            RecordingSession.scenario_id == scenario_id,
            RecordingSession.status.in_(["pending", "failed"]),
        ).order_by(RecordingSession.created_at.desc())
    ).scalar_one_or_none()

    if existing:
        existing.status = "pending"
        existing.started_at = None
        existing.completed_at = None
        db.commit()
        db.refresh(existing)
        return RecorderSessionResponse(id=existing.id, status=existing.status)

    session = RecordingSession(
        project_id=project.id,
        scenario_id=scenario_id,
        status="pending",
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return RecorderSessionResponse(id=session.id, status=session.status)


def start_session(
    db: Session, project: Project, session_id: uuid.UUID
) -> RecorderSessionResponse:
    session = db.execute(
        select(RecordingSession).where(
            RecordingSession.id == session_id,
            RecordingSession.project_id == project.id,
        )
    ).scalar_one_or_none()
    if session is None:
        raise ValueError("Session not found")

    session.status = "in_progress"
    session.started_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(session)
    return RecorderSessionResponse(id=session.id, status=session.status)


def complete_session(
    db: Session, project: Project, session_id: uuid.UUID
) -> RecorderSessionResponse:
    session = db.execute(
        select(RecordingSession).where(
            RecordingSession.id == session_id,
            RecordingSession.project_id == project.id,
        )
    ).scalar_one_or_none()
    if session is None:
        raise ValueError("Session not found")

    session.status = "completed"
    session.completed_at = datetime.now(timezone.utc)
    db.commit()

    # Mark the scenario as completed
    scenario = db.get(TestScenario, session.scenario_id)
    if scenario:
        scenario.status = "completed"
        scenario.completed_by = project.owner_id
        db.commit()

    db.refresh(session)
    return RecorderSessionResponse(id=session.id, status=session.status)


def fail_session(
    db: Session, project: Project, session_id: uuid.UUID
) -> RecorderSessionResponse:
    session = db.execute(
        select(RecordingSession).where(
            RecordingSession.id == session_id,
            RecordingSession.project_id == project.id,
        )
    ).scalar_one_or_none()
    if session is None:
        raise ValueError("Session not found")

    session.status = "failed"
    db.commit()
    db.refresh(session)
    return RecorderSessionResponse(id=session.id, status=session.status)


def upsert_route(
    db: Session, project: Project, payload: RecorderRouteUpsert
) -> RecorderRouteResponse:
    """
    Upsert into discovered_routes (global registry) and create a route_variant
    for this specific scenario visit.
    """
    path = _url_path(payload.url)
    base = _recordings_base() / str(project.id) / str(payload.session_id)

    # ── Global route registry ──────────────────────────────────────────────
    route = db.execute(
        select(DiscoveredRoute).where(
            DiscoveredRoute.project_id == project.id,
            DiscoveredRoute.path == path,
        )
    ).scalar_one_or_none()

    is_new_route = route is None
    screenshot_path: str | None = None
    html_path: str | None = None

    if payload.screenshot_base64:
        filename = f"{path.replace('/', '_') or 'root'}.png"
        screenshot_path = _save_file(
            payload.screenshot_base64,
            base / "routes",
            filename,
        )

    if payload.html_base64:
        filename = f"{path.replace('/', '_') or 'root'}.html"
        html_path = _save_file(
            payload.html_base64,
            base / "routes",
            filename,
        )

    if is_new_route:
        route = DiscoveredRoute(
            project_id=project.id,
            path=path,
            full_url=payload.url,
            page_title=payload.title,
            html_path=html_path,
            accessibility_tree=payload.accessibility_tree,
            interactive_elements=payload.interactive_elements,
            screenshot_path=screenshot_path,
        )
        db.add(route)
        db.flush()  # get route.id before creating variant
    else:
        # Enrich existing route: merge interactive_elements superset
        if payload.interactive_elements:
            existing_selectors: set[str] = {
                el.get("selector", "")
                for el in (route.interactive_elements or [])
            }
            new_elements = [
                el
                for el in payload.interactive_elements
                if el.get("selector", "") not in existing_selectors
            ]
            route.interactive_elements = (route.interactive_elements or []) + new_elements

        if payload.accessibility_tree:
            route.accessibility_tree = payload.accessibility_tree

        if html_path:
            route.html_path = html_path
        if screenshot_path:
            route.screenshot_path = screenshot_path

        route.page_title = payload.title or route.page_title
        route.last_updated_at = datetime.now(timezone.utc)

    # ── Route variant ──────────────────────────────────────────────────────
    variant_screenshot: str | None = None
    variant_html: str | None = None

    if payload.screenshot_base64:
        vid = str(uuid.uuid4())[:8]
        variant_screenshot = _save_file(
            payload.screenshot_base64,
            base / "variants",
            f"{vid}_{path.replace('/', '_') or 'root'}.png",
        )

    if payload.html_base64:
        vid = str(uuid.uuid4())[:8]
        variant_html = _save_file(
            payload.html_base64,
            base / "variants",
            f"{vid}_{path.replace('/', '_') or 'root'}.html",
        )

    variant = RouteVariant(
        route_id=route.id,
        scenario_id=payload.scenario_id,
        recording_session_id=payload.session_id,
        project_id=project.id,
        html_path=variant_html,
        accessibility_tree=payload.accessibility_tree,
        interactive_elements=payload.interactive_elements,
        screenshot_path=variant_screenshot,
        network_calls=payload.network_calls,
    )
    db.add(variant)
    db.commit()
    db.refresh(route)
    db.refresh(variant)

    return RecorderRouteResponse(
        route_id=route.id,
        variant_id=variant.id,
        is_new_route=is_new_route,
    )


def append_step(
    db: Session,
    project: Project,
    session_id: uuid.UUID,
    payload: RecorderStepCreate,
) -> RecorderStepResponse:
    session = db.execute(
        select(RecordingSession).where(
            RecordingSession.id == session_id,
            RecordingSession.project_id == project.id,
        )
    ).scalar_one_or_none()
    if session is None:
        raise ValueError("Session not found")

    base = _recordings_base() / str(project.id) / str(session_id) / "steps"
    screenshot_path: str | None = None

    if payload.screenshot_base64:
        screenshot_path = _save_file(
            payload.screenshot_base64,
            base,
            f"step_{payload.step_index:04d}.png",
        )

    step = ScenarioStep(
        scenario_id=session.scenario_id,
        recording_session_id=session_id,
        project_id=project.id,
        step_index=payload.step_index,
        action_type=payload.action_type,
        url=payload.url,
        selector=payload.selector,
        value=payload.value,
        element_text=payload.element_text,
        element_type=payload.element_type,
        screenshot_path=screenshot_path,
        network_calls=payload.network_calls,
    )
    db.add(step)
    db.commit()
    db.refresh(step)

    return RecorderStepResponse(id=step.id)


def get_recorder_script(project_id: uuid.UUID, recorder_token: str, server_url: str) -> str:
    """
    Return the recorder Python script as a string, with project-specific
    values already embedded. The frontend calls this to get the script content,
    which it then serves via a download endpoint.
    """
    # Read the template from disk (you store recorder_template.py in your repo)
    template_path = Path(__file__).parent.parent.parent / "recorder_template.py"
    template = template_path.read_text(encoding="utf-8")

    return (
        template
        .replace("__PROJECT_ID__", str(project_id))
        .replace("__RECORDER_TOKEN__", recorder_token)
        .replace("__SERVER_URL__", server_url)
    )