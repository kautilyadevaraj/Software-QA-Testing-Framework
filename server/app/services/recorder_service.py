"""Business logic for the recorder API — called by the Python recorder script."""

from __future__ import annotations

import base64
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.project import (
    HighLevelScenario,
    Project,
)
from app.models.scenario import (
    DiscoveredRoute,
    RecordedAssertionCandidate,
    RecordedRouteTransition,
    RecordingFlow,
    RecordingSession,
    RouteVariant,
    ScenarioStep,
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
    base = Path(getattr(settings, "RECORDINGS_BASE_PATH", "recordings"))
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


def _url_path_with_query(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    path = parsed.path.rstrip("/") or "/"
    return f"{path}?{parsed.query}" if parsed.query else path


def _query_params(url: str | None) -> dict[str, str]:
    if not url:
        return {}
    try:
        return dict(parse_qsl(urlparse(url).query, keep_blank_values=True))
    except Exception:
        return {}


def _classify_input_value(payload: RecorderStepCreate) -> str | None:
    if payload.input_value_kind:
        return payload.input_value_kind
    if payload.value in (None, ""):
        return "empty"
    selector_bits = " ".join(
        str(v or "")
        for v in (
            payload.selector,
            payload.element_text,
            payload.accessible_name,
            payload.label,
            payload.input_type,
        )
    ).lower()
    if "password" in selector_bits or "email" in selector_bits or "username" in selector_bits:
        return "credential"
    if payload.action_type == "select":
        return "option_value"
    return "literal"


def _latest_route_variant_id_for_url(
    db: Session,
    *,
    project_id: uuid.UUID,
    session_id: uuid.UUID,
    url: str | None,
) -> uuid.UUID | None:
    if not url:
        return None
    path = _url_path(url)
    return db.execute(
        select(RouteVariant.id)
        .join(DiscoveredRoute, RouteVariant.route_id == DiscoveredRoute.id)
        .where(
            RouteVariant.project_id == project_id,
            RouteVariant.recording_session_id == session_id,
            DiscoveredRoute.path == path,
        )
        .order_by(RouteVariant.snapshot_index.desc().nullslast(), RouteVariant.captured_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def _with_route_variant_context(
    semantic_context: dict | None,
    *,
    before_id: uuid.UUID | None,
    after_id: uuid.UUID | None,
) -> dict | None:
    if semantic_context is None and before_id is None and after_id is None:
        return None
    context = dict(semantic_context or {})
    context["route_variants"] = {
        "before_id": str(before_id) if before_id else None,
        "after_id": str(after_id) if after_id else None,
    }
    return context


def _latest_flow_for_session(db: Session, session_id: uuid.UUID) -> RecordingFlow | None:
    return db.execute(
        select(RecordingFlow)
        .where(RecordingFlow.recording_id == session_id)
        .order_by(RecordingFlow.flow_index.desc())
        .limit(1)
    ).scalar_one_or_none()


def _transition_type(payload: RecorderStepCreate, before_id: uuid.UUID | None, after_id: uuid.UUID | None) -> tuple[str, float]:
    before_path = _url_path_with_query(payload.url_before or payload.url)
    after_path = _url_path_with_query(payload.url_after)
    if before_path and after_path and before_path != after_path:
        return "url_change", 1.0
    semantic = payload.semantic_context or {}
    navigation = semantic.get("navigation") if isinstance(semantic, dict) else {}
    if isinstance(navigation, dict) and navigation.get("caused_navigation"):
        return "url_change", 1.0
    if before_id and after_id and before_id != after_id:
        return "dom_change", 0.9
    if payload.action_type in {"fill", "select", "check", "uncheck", "slide"}:
        return "state_change", 0.85
    return "no_url_change", 0.7


def _create_transition(
    db: Session,
    *,
    session: RecordingSession,
    step: ScenarioStep,
    payload: RecorderStepCreate,
    before_id: uuid.UUID | None,
    after_id: uuid.UUID | None,
) -> None:
    transition_type, confidence = _transition_type(payload, before_id, after_id)
    metadata = dict(payload.semantic_context or {})
    metadata.update({
        "query_params": {
            "from": _query_params(payload.url_before or payload.url),
            "to": _query_params(payload.url_after),
        },
        "url_changed": transition_type == "url_change",
        "dom_changed": before_id is not None and after_id is not None and before_id != after_id,
    })
    db.add(
        RecordedRouteTransition(
            recording_id=session.id,
            flow_id=payload.flow_id,
            hls_id=session.scenario_id,
            step_id=step.id,
            step_index=step.step_index,
            from_url=payload.url_before or payload.url,
            from_path=_url_path_with_query(payload.url_before or payload.url),
            to_url=payload.url_after,
            to_path=_url_path_with_query(payload.url_after),
            action_type=payload.action_type,
            selector=payload.selector,
            element_text=payload.element_text,
            accessible_name=payload.accessible_name,
            transition_type=transition_type,
            confidence=confidence,
            before_snapshot_id=before_id,
            after_snapshot_id=after_id,
            metadata_json=metadata,
        )
    )


def _store_assertion_candidates(
    db: Session,
    *,
    payload: RecorderRouteUpsert,
    snapshot_id: uuid.UUID,
) -> None:
    for index, candidate in enumerate(payload.assertion_candidates or []):
        if not isinstance(candidate, dict):
            continue
        db.add(
            RecordedAssertionCandidate(
                recording_id=payload.session_id,
                flow_id=payload.flow_id,
                hls_id=payload.scenario_id,
                snapshot_id=snapshot_id,
                candidate_index=index,
                kind=str(candidate.get("kind") or "ui_text"),
                selector=candidate.get("selector"),
                text=candidate.get("text"),
                confidence=float(candidate.get("confidence") or 0.7),
                metadata_json={
                    k: v
                    for k, v in candidate.items()
                    if k not in {"kind", "selector", "text", "confidence"}
                } or None,
            )
        )


def _backfill_step_route_variant_links(
    db: Session,
    *,
    project_id: uuid.UUID,
    session_id: uuid.UUID,
    path: str,
    variant_id: uuid.UUID,
) -> None:
    steps = list(
        db.execute(
            select(ScenarioStep).where(
                ScenarioStep.project_id == project_id,
                ScenarioStep.recording_session_id == session_id,
            )
        ).scalars()
    )
    for step in steps:
        changed = False
        if (
            step.route_variant_before_id is None
            and step.url_before
            and _url_path(step.url_before) == path
        ):
            step.route_variant_before_id = variant_id
            changed = True
        if (
            step.route_variant_after_id is None
            and step.url_after
            and _url_path(step.url_after) == path
        ):
            step.route_variant_after_id = variant_id
            changed = True
        if changed:
            step.semantic_context = _with_route_variant_context(
                step.semantic_context,
                before_id=step.route_variant_before_id,
                after_id=step.route_variant_after_id,
            )
            db.add(step)


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
            select(HighLevelScenario).where(
                HighLevelScenario.project_id == project.id
            ).order_by(HighLevelScenario.created_at)
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
        select(HighLevelScenario).where(
            HighLevelScenario.id == scenario_id,
            HighLevelScenario.project_id == project.id,
        )
    ).scalar_one_or_none()
    if scenario is None:
        raise ValueError("Scenario not found in this project")

    session = RecordingSession(
        project_id=project.id,
        scenario_id=scenario_id,
        status="pending",
    )
    db.add(session)
    db.flush()

    flow = RecordingFlow(
        recording_id=session.id,
        project_id=project.id,
        hls_id=scenario_id,
        flow_index=0,
        status="pending",
        metadata_json={"contract_version": 2},
    )
    db.add(flow)
    
    # Clear the trigger flag if this was the launched scenario
    if project.active_launch_scenario_id == scenario_id:
        project.active_launch_scenario_id = None
        db.add(project)

    db.commit()
    db.refresh(session)
    db.refresh(flow)
    return RecorderSessionResponse(id=session.id, status=session.status, flow_id=flow.id)


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
    flow = _latest_flow_for_session(db, session.id)
    if flow:
        flow.status = "in_progress"
        flow.started_at = session.started_at
        db.add(flow)
    db.commit()
    db.refresh(session)
    return RecorderSessionResponse(id=session.id, status=session.status, flow_id=flow.id if flow else None)


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
    flow = _latest_flow_for_session(db, session.id)
    if flow:
        action_count = db.execute(
            select(ScenarioStep.id).where(
                ScenarioStep.recording_session_id == session.id,
                ScenarioStep.flow_id == flow.id,
            )
        ).first()
        flow.status = "completed"
        flow.completed_at = session.completed_at
        flow.phase3_ready = bool(action_count)
        db.add(flow)
    db.commit()

    # Mark the scenario as completed
    scenario = db.get(HighLevelScenario, session.scenario_id)
    if scenario:
        scenario.status = "completed"
        scenario.completed_by = project.owner_id
        db.commit()

    db.refresh(session)
    return RecorderSessionResponse(id=session.id, status=session.status, flow_id=flow.id if flow else None)


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
    flow = _latest_flow_for_session(db, session.id)
    if flow:
        flow.status = "failed"
        db.add(flow)
    db.commit()
    db.refresh(session)
    return RecorderSessionResponse(id=session.id, status=session.status, flow_id=flow.id if flow else None)


def get_session_status(
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

    flow = _latest_flow_for_session(db, session.id)
    return RecorderSessionResponse(id=session.id, status=session.status, flow_id=flow.id if flow else None)


def upsert_route(
    db: Session, project: Project, payload: RecorderRouteUpsert
) -> RecorderRouteResponse:
    """
    Upsert into discovered_routes (global registry) and create a route_variant
    for this specific scenario visit.
    """
    path = _url_path(payload.url)
    base = _recordings_base() / str(project.id) / str(payload.session_id)
    flow = _latest_flow_for_session(db, payload.session_id)
    flow_id = payload.flow_id or (flow.id if flow else None)
    payload = payload.model_copy(update={"flow_id": flow_id})
    snapshot_label = f"{payload.snapshot_index:04d}" if payload.snapshot_index is not None else str(uuid.uuid4())[:8]

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
        filename = f"{snapshot_label}_{path.replace('/', '_') or 'root'}.png"
        screenshot_path = _save_file(
            payload.screenshot_base64,
            base / "routes",
            filename,
        )

    if payload.html_base64:
        filename = f"{snapshot_label}_{path.replace('/', '_') or 'root'}.html"
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
        variant_screenshot = _save_file(
            payload.screenshot_base64,
            base / "variants",
            f"{snapshot_label}_{path.replace('/', '_') or 'root'}.png",
        )

    if payload.html_base64:
        variant_html = _save_file(
            payload.html_base64,
            base / "variants",
            f"{snapshot_label}_{path.replace('/', '_') or 'root'}.html",
        )

    variant = RouteVariant(
        route_id=route.id,
        scenario_id=payload.scenario_id,
        recording_session_id=payload.session_id,
        flow_id=flow_id,
        project_id=project.id,
        html_path=variant_html,
        accessibility_tree=payload.accessibility_tree,
        interactive_elements=payload.interactive_elements,
        screenshot_path=variant_screenshot,
        network_calls=payload.network_calls,
        snapshot_index=payload.snapshot_index,
        snapshot_kind=payload.snapshot_kind,
        assertion_candidates=payload.assertion_candidates,
        metadata_json=payload.metadata_json,
    )
    db.add(variant)
    db.flush()
    _store_assertion_candidates(db, payload=payload, snapshot_id=variant.id)
    if payload.snapshot_index is None:
        _backfill_step_route_variant_links(
            db,
            project_id=project.id,
            session_id=payload.session_id,
            path=path,
            variant_id=variant.id,
        )
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
    flow = _latest_flow_for_session(db, session.id)
    flow_id = payload.flow_id or (flow.id if flow else None)
    payload = payload.model_copy(update={"flow_id": flow_id})

    base = _recordings_base() / str(project.id) / str(session_id) / "steps"
    screenshot_path: str | None = None

    step_index = payload.step_index
    used_indices: set[int] = set(
        db.execute(
            select(ScenarioStep.step_index).where(
                ScenarioStep.recording_session_id == session_id,
            )
        ).scalars()
    )
    while step_index in used_indices:
        step_index += 1

    if payload.screenshot_base64:
        screenshot_path = _save_file(
            payload.screenshot_base64,
            base,
            f"step_{step_index:04d}.png",
        )

    route_variant_before_id = (
        payload.route_variant_before_id
        or _latest_route_variant_id_for_url(
            db,
            project_id=project.id,
            session_id=session_id,
            url=payload.url_before or payload.url,
        )
    )
    route_variant_after_id = (
        payload.route_variant_after_id
        or _latest_route_variant_id_for_url(
            db,
            project_id=project.id,
            session_id=session_id,
            url=payload.url_after,
        )
    )

    step = ScenarioStep(
        scenario_id=session.scenario_id,
        recording_session_id=session_id,
        flow_id=flow_id,
        project_id=project.id,
        step_index=step_index,
        action_type=payload.action_type,
        url=payload.url,
        selector=payload.selector,
        selector_candidates=payload.selector_candidates,
        value=payload.value,
        input_value_kind=_classify_input_value(payload),
        element_text=payload.element_text,
        element_type=payload.element_type,
        selector_stability=payload.selector_stability,
        playwright_locator=payload.playwright_locator,
        accessible_name=payload.accessible_name,
        role=payload.role,
        label=payload.label,
        input_type=payload.input_type,
        url_before=payload.url_before,
        url_after=payload.url_after,
        caused_navigation=payload.caused_navigation,
        route_variant_before_id=route_variant_before_id,
        route_variant_after_id=route_variant_after_id,
        semantic_context=_with_route_variant_context(
            payload.semantic_context,
            before_id=route_variant_before_id,
            after_id=route_variant_after_id,
        ),
        screenshot_path=screenshot_path,
        network_calls=payload.network_calls,
    )
    db.add(step)
    db.flush()
    _create_transition(
        db,
        session=session,
        step=step,
        payload=payload,
        before_id=route_variant_before_id,
        after_id=route_variant_after_id,
    )
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
    action_js_path = Path(__file__).parent.parent.parent / "recorder_action_capture.js"
    action_js = action_js_path.read_text(encoding="utf-8")
    template = re.sub(
        r'ACTION_CAPTURE_JS = """\n.*?\n"""',
        lambda _match: f"ACTION_CAPTURE_JS = {action_js!r}",
        template,
        count=1,
        flags=re.DOTALL,
    )

    return (
        template
        .replace("__PROJECT_ID__", str(project_id))
        .replace("__RECORDER_TOKEN__", recorder_token)
        .replace("__SERVER_URL__", server_url)
        .replace("__STORE_PASSWORD_VALUES__", "True" if settings.recorder_store_password_values else "False")
        .replace("__SCREENSHOT_INDICATOR__", "True" if settings.recorder_screenshot_indicator else "False")
    )
