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


_VALID_PLAYWRIGHT_ROLES = {
    "alert",
    "alertdialog",
    "article",
    "banner",
    "button",
    "cell",
    "checkbox",
    "columnheader",
    "combobox",
    "contentinfo",
    "definition",
    "dialog",
    "directory",
    "document",
    "feed",
    "figure",
    "form",
    "grid",
    "gridcell",
    "group",
    "heading",
    "img",
    "link",
    "list",
    "listbox",
    "listitem",
    "log",
    "main",
    "marquee",
    "math",
    "menu",
    "menubar",
    "menuitem",
    "menuitemcheckbox",
    "menuitemradio",
    "meter",
    "navigation",
    "none",
    "note",
    "option",
    "presentation",
    "progressbar",
    "radio",
    "radiogroup",
    "region",
    "row",
    "rowgroup",
    "rowheader",
    "scrollbar",
    "search",
    "searchbox",
    "separator",
    "slider",
    "spinbutton",
    "status",
    "switch",
    "tab",
    "table",
    "tablist",
    "tabpanel",
    "term",
    "textbox",
    "timer",
    "toolbar",
    "tooltip",
    "tree",
    "treegrid",
    "treeitem",
}

_HTML_TAG_TO_ROLE = {
    "a": "link",
    "button": "button",
    "select": "combobox",
    "textarea": "textbox",
}

_TEXTBOX_INPUT_TYPES = {
    "",
    "date",
    "datetime-local",
    "email",
    "month",
    "number",
    "password",
    "search",
    "tel",
    "text",
    "time",
    "url",
    "week",
}


def _normalize_role(role: str | None, element_type: str | None, input_type: str | None) -> str | None:
    """Convert browser tag-ish roles to valid ARIA roles usable by Playwright."""
    raw_role = str(role or "").strip().lower()
    tag = str(element_type or "").strip().lower()
    kind = str(input_type or "").strip().lower()

    if raw_role in _VALID_PLAYWRIGHT_ROLES and raw_role not in {"none", "presentation"}:
        return raw_role
    if raw_role in {"a", "anchor"}:
        return "link"
    if raw_role == "input":
        if kind in {"button", "submit", "reset", "image"}:
            return "button"
        if kind == "checkbox":
            return "checkbox"
        if kind == "radio":
            return "radio"
        if kind == "range":
            return "slider"
        if kind in _TEXTBOX_INPUT_TYPES:
            return "textbox"
    if raw_role in {"span", "div", "label", "form"}:
        raw_role = ""

    if tag in _HTML_TAG_TO_ROLE:
        return _HTML_TAG_TO_ROLE[tag]
    if tag == "input":
        return _normalize_role("input", element_type, input_type)
    return raw_role if raw_role in _VALID_PLAYWRIGHT_ROLES else None


def _normalize_playwright_locator(locator: str | None, role: str | None) -> str | None:
    if not locator:
        return locator
    normalized_role = str(role or "").strip()
    match = re.match(r"page\.getByRole\('([^']+)'", locator)
    if not match:
        return locator
    original_role = match.group(1)
    if not normalized_role or normalized_role not in _VALID_PLAYWRIGHT_ROLES:
        return None
    if original_role == normalized_role:
        return locator
    return locator.replace(f"getByRole('{original_role}'", f"getByRole('{normalized_role}'", 1)


def _semantic_parent_context(payload: RecorderStepCreate) -> dict | None:
    context = payload.semantic_context or {}
    if not isinstance(context, dict):
        return None
    parent = context.get("parent_context")
    element = context.get("element")
    if not isinstance(parent, dict) and isinstance(element, dict):
        parent = element.get("parent_context")
    return parent if isinstance(parent, dict) else None


def _actionable_parent_update(payload: RecorderStepCreate) -> dict[str, object]:
    """Prefer the clickable ancestor when the browser event hit a child node.

    Example: user clicks a counter badge `<span>` inside an `<a>`. The automation
    contract should click the link, not the child text span.
    """
    if payload.action_type not in {"click", "submit"}:
        return {}
    parent = _semantic_parent_context(payload)
    if not parent:
        return {}
    parent_selector = str(parent.get("selector") or "").strip()
    parent_tag = str(parent.get("tag") or "").strip().lower()
    parent_role = _normalize_role(str(parent.get("role") or ""), parent_tag, None)
    if not parent_selector or parent_selector == payload.selector:
        return {}
    if parent_role not in {"button", "link", "checkbox", "radio", "menuitem", "tab"}:
        return {}

    candidates = [parent_selector]
    for candidate in payload.selector_candidates or []:
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    text = str(parent.get("text") or parent.get("label") or payload.element_text or "")
    update: dict[str, object] = {
        "selector": parent_selector,
        "selector_candidates": candidates,
        "element_type": parent_tag or payload.element_type,
        "role": parent_role,
        "element_text": text,
        "accessible_name": str(parent.get("label") or parent.get("text") or payload.accessible_name or ""),
    }
    return update


# ── Noise detection ───────────────────────────────────────────────────────

_AD_TRACKER_HOST_PATTERNS = {
    "googleads", "doubleclick", "pagead", "adsystem",
    "taboola", "outbrain", "analytics", "tracking",
}
_CAPTCHA_HOST_PATTERNS = {
    "captcha", "recaptcha", "hcaptcha",
    "security-check", "bot-check", "challenge",
}
_CONSENT_HOST_PATTERNS = {
    "cookiebot", "onetrust", "cookie-consent", "trustarc",
}


def _is_noise_step(
    payload: RecorderStepCreate,
    session_origin: str | None,
) -> tuple[bool, str | None]:
    """Return (is_noise, noise_reason).  Noise steps are stored with is_noise=True,
    never silently dropped — Phase 3 skips them."""
    selector_str = str(payload.selector or "").lower()
    semantic_bits = " ".join(
        str(v or "")
        for v in (
            payload.element_text,
            payload.accessible_name,
            payload.label,
            payload.element_type,
        )
    ).lower()
    if "iframe" in selector_str and "advertisement" in semantic_bits:
        return True, "ad_iframe_element:advertisement"

    url = payload.url_before or payload.url or ""
    if not url:
        return False, None

    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
    except Exception:
        return False, None

    # Check if external domain (host differs from session origin)
    if session_origin:
        try:
            origin_host = (urlparse(session_origin).hostname or "").lower()
            is_external = host and host != origin_host and not host.endswith(f".{origin_host}")
        except Exception:
            is_external = False
    else:
        is_external = False

    # Ad/analytics host patterns
    for pattern in _AD_TRACKER_HOST_PATTERNS:
        if pattern in host:
            return True, f"ad_or_tracker_domain:{host}"

    # Captcha/bot-check host or path patterns
    path_lower = (parsed.path or "").lower()
    for pattern in _CAPTCHA_HOST_PATTERNS:
        if pattern in host or pattern in path_lower:
            return True, f"captcha_or_security:{host}{path_lower}"

    # Cookie/consent overlay host patterns
    for pattern in _CONSENT_HOST_PATTERNS:
        if pattern in host:
            return True, f"consent_overlay:{host}"

    # Selector chain contains an ad iframe with external domain
    if "iframe" in selector_str and is_external:
        return True, f"ad_iframe_element:{host}"

    return False, None


def _is_security_blocked_url(url: str) -> bool:
    """Return True if the page URL itself is a bot-check or captcha page."""
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        path = (parsed.path or "").lower()
        for pattern in _CAPTCHA_HOST_PATTERNS:
            if pattern in host or pattern in path:
                return True
    except Exception:
        pass
    return False


# ── Selector quality reason ────────────────────────────────────────────────

def _selector_quality_reason(selector: str | None, playwright_locator: str | None = None) -> str | None:
    """Derive a machine-readable quality reason from the primary selector string."""
    if playwright_locator and ("getByRole" in playwright_locator or "getByLabel" in playwright_locator):
        return "role_name"
    if not selector:
        return None
    s = selector.strip()
    lower = s.lower()
    if re.search(r'\[data-testid|\[data-test|\[data-cy|\[data-qa', s):
        return "data_attr"
    if s.startswith("#") and not re.search(r'[0-9a-f-]{16,}', s, re.IGNORECASE):
        return "stable_id"
    if re.search(r'\[role=|\[aria-label=', s):
        return "role_name"
    if s.startswith("label:"):
        return "label"
    if re.search(r'\[placeholder=', s):
        return "placeholder"
    if re.search(r'\[href=', s):
        href_match = re.search(r"""href\s*=\s*["']([^"']+)["']""", s, re.IGNORECASE)
        href = href_match.group(1).strip().lower() if href_match else ""
        if href in {"#", "javascript:void(0)", "javascript:;"}:
            return "structural_fallback"
        return "href"
    if re.search(r':nth-of-type\(|:nth-child\(', lower):
        return "structural_fallback"
    return "structural_fallback"


def _recording_quality_failure_reasons(quality: dict | None) -> list[str]:
    quality = quality or {}
    reasons: list[str] = []
    total = int(quality.get("total_steps") or 0)
    stable = int(quality.get("stable_selector_count") or 0)
    noise = int(quality.get("noise_step_count") or 0)
    assertions = int(quality.get("assertion_candidate_count") or 0)

    if total < 3:
        reasons.append("too_few_recorded_steps")
    if total > 0 and stable / total < 0.5:
        reasons.append("insufficient_stable_selectors")
    if total > 0 and noise / total > 0.3:
        reasons.append("too_much_noise")
    if bool(quality.get("blocked_by_security")):
        reasons.append("blocked_by_security")
    if assertions < 1:
        reasons.append("missing_assertion_candidates")
    return reasons


def _quality_allows_scenario_completion(quality: dict | None) -> bool:
    return bool((quality or {}).get("phase3_ready")) and not _recording_quality_failure_reasons(quality)


# ── Semantic field identity ────────────────────────────────────────────────

def _build_field_identity(payload: RecorderStepCreate) -> dict | None:
    """Build field_identity for fill/select/check/uncheck steps to disambiguate
    same-placeholder fields across different routes."""
    if payload.action_type not in {"fill", "select", "check", "uncheck"}:
        return None

    route_path = _url_path_with_query(payload.url_before or payload.url) or None
    if not route_path:
        return None

    context = payload.semantic_context or {}
    # label may be directly on payload
    field_label = payload.label or None
    placeholder_val = None
    form_title = None
    form_action = None
    section_heading = None
    preceding_label_text = None
    parent_form_selector = None

    # Extract from semantic_context.element or page_context
    element = context.get("element") if isinstance(context, dict) else None
    if isinstance(element, dict):
        field_label = field_label or element.get("label") or None
        placeholder_val = element.get("type") or None  # type is input type; placeholder stored separately

    # Extract placeholder from selector string
    placeholder_match = re.search(r'\[placeholder="([^"]+)"', payload.selector or "")
    if placeholder_match:
        placeholder_val = placeholder_match.group(1)

    # Form context from page_context
    page_ctx = context.get("page_context") if isinstance(context, dict) else None
    if isinstance(page_ctx, dict):
        forms = page_ctx.get("forms") or []
        if forms:
            first_form = forms[0] if isinstance(forms[0], dict) else {}
            form_action = first_form.get("action") or None
            form_title = first_form.get("submit_text") or None
    headings = (page_ctx or {}).get("headings") if isinstance(page_ctx, dict) else None
    if headings and isinstance(headings, list) and headings:
        first_h = headings[0] if isinstance(headings[0], dict) else {}
        section_heading = first_h.get("text") or None

    # Parent context for preceding label text
    parent = _semantic_parent_context(payload)
    if parent:
        preceding_label_text = parent.get("label") or parent.get("text") or None
        parent_form_selector = parent.get("selector") or None

    return {
        "route_path": route_path,
        "form_title": form_title,
        "field_label": field_label,
        "placeholder": placeholder_val,
        "form_action": form_action,
        "section_heading": section_heading,
        "preceding_label_text": preceding_label_text,
        "parent_form_selector": parent_form_selector,
    }


def _normalize_step_payload(
    payload: RecorderStepCreate,
    session_origin: str | None = None,
) -> RecorderStepCreate:
    """Harden recorder data before it becomes Phase 3 grounding evidence."""
    update: dict[str, object] = {}
    update.update(_actionable_parent_update(payload))

    action = str(payload.action_type or "").lower()
    input_type = str(update.get("input_type") or payload.input_type or "").lower()
    element_type = str(update.get("element_type") or payload.element_type or "").lower()

    # Text fills are not route-owning actions. If the browser reports a later
    # submit navigation on the preceding fill event, keep the fill same-route.
    if action == "fill" and payload.url_before and payload.url_after and _url_path_with_query(payload.url_before) != _url_path_with_query(payload.url_after):
        update["url_after"] = payload.url_before
        update["caused_navigation"] = False
        if payload.semantic_context:
            context = dict(payload.semantic_context)
            navigation = dict(context.get("navigation") or {})
            navigation.update({"to": None, "caused_navigation": False})
            context["navigation"] = navigation
            page = dict(context.get("page") or {})
            page["url_after"] = payload.url_before
            context["page"] = page
            update["semantic_context"] = context

    role = _normalize_role(
        str(update.get("role") or payload.role or ""),
        str(update.get("element_type") or payload.element_type or ""),
        input_type,
    )
    update["role"] = role
    update["playwright_locator"] = _normalize_playwright_locator(payload.playwright_locator, role)

    # Noise detection
    is_noise, noise_reason = _is_noise_step(payload, session_origin)
    update["is_noise"] = is_noise
    update["noise_reason"] = noise_reason

    # Selector quality reason
    primary_selector = str(update.get("selector") or payload.selector or "")
    pw_locator = str(update.get("playwright_locator") or payload.playwright_locator or "")
    update["selector_quality_reason"] = _selector_quality_reason(primary_selector or None, pw_locator or None)

    # Semantic field identity (stored back into semantic_context)
    # Use a merged copy of payload for field_identity so it sees the updated url_after etc.
    merged_payload = payload.model_copy(update=update) if update else payload
    field_identity = _build_field_identity(merged_payload)
    if field_identity:
        existing_context = dict(update.get("semantic_context") or payload.semantic_context or {})
        existing_context["field_identity"] = field_identity
        update["semantic_context"] = existing_context

    return payload.model_copy(update=update) if update else payload


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
        # Confidence gate: skip candidates below 0.3 threshold
        confidence = float(candidate.get("confidence") or 0.7)
        if confidence < 0.3:
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
                confidence=confidence,
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


def _compute_recording_quality(db: Session, flow: RecordingFlow) -> dict:
    """Compute a quality summary dict for a completed flow and determine phase3_ready."""
    steps = list(
        db.execute(
            select(ScenarioStep).where(
                ScenarioStep.flow_id == flow.id,
            )
        ).scalars()
    )
    total_steps = len(steps)
    stable_selector_count = sum(1 for s in steps if s.selector_stability == "high")
    structural_selector_count = sum(1 for s in steps if s.selector_stability == "low")
    noise_step_count = sum(1 for s in steps if getattr(s, "is_noise", False))
    route_variant_count = len(list(db.execute(select(RouteVariant.id).where(RouteVariant.flow_id == flow.id)).scalars()))
    assertion_candidate_count = len(list(
        db.execute(
            select(RecordedAssertionCandidate.id).where(
                RecordedAssertionCandidate.flow_id == flow.id,
            )
        ).scalars()
    ))
    missing_after_snapshot_count = sum(1 for s in steps if s.route_variant_after_id is None)

    flow_meta = dict(flow.metadata_json or {})
    blocked_by_security = bool(flow_meta.get("blocked_by_security", False))

    # phase3_ready criteria (all must pass)
    phase3_ready = (
        total_steps >= 3
        and (stable_selector_count / total_steps >= 0.5 if total_steps > 0 else False)
        and (noise_step_count / total_steps <= 0.3 if total_steps > 0 else True)
        and not blocked_by_security
        and assertion_candidate_count >= 1
    )

    return {
        "total_steps": total_steps,
        "stable_selector_count": stable_selector_count,
        "structural_selector_count": structural_selector_count,
        "noise_step_count": noise_step_count,
        "route_variant_count": route_variant_count,
        "assertion_candidate_count": assertion_candidate_count,
        "missing_after_snapshot_count": missing_after_snapshot_count,
        "blocked_by_security": blocked_by_security,
        "phase3_ready": phase3_ready,
    }


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
    quality: dict | None = None
    if flow:
        flow.status = "completed"
        flow.completed_at = session.completed_at
        db.add(flow)
        db.flush()  # ensure flow fields are committed before quality compute
        quality = _compute_recording_quality(db, flow)
        flow_meta = dict(flow.metadata_json or {})
        flow_meta["quality_summary"] = quality
        flow_meta["quality_failure_reasons"] = _recording_quality_failure_reasons(quality)
        flow.metadata_json = flow_meta
        flow.phase3_ready = quality["phase3_ready"]
        db.add(flow)
    db.commit()

    # Only mark the HLS complete when Phase 2 evidence is strong enough for Phase 3.
    scenario = db.get(HighLevelScenario, session.scenario_id)
    if scenario:
        if _quality_allows_scenario_completion(quality):
            scenario.status = "completed"
            scenario.completed_by = project.owner_id
        else:
            scenario.status = "pending"
            scenario.completed_by = None
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

    # ── Security block detection ───────────────────────────────────────────
    if _is_security_blocked_url(payload.url) and flow_id:
        current_flow = db.get(RecordingFlow, flow_id)
        if current_flow:
            flow_meta = dict(current_flow.metadata_json or {})
            if not flow_meta.get("blocked_by_security"):
                flow_meta["blocked_by_security"] = True
                current_flow.metadata_json = flow_meta
                db.add(current_flow)

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
    # Determine session origin URL for noise detection
    session_origin: str | None = None
    project_obj = db.get(Project, project.id)
    if project_obj and getattr(project_obj, "url", None):
        session_origin = project_obj.url
    payload = _normalize_step_payload(payload, session_origin=session_origin)

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
        # Phase 2 step-quality fields
        is_noise=payload.is_noise,
        noise_reason=payload.noise_reason,
        selector_quality_reason=payload.selector_quality_reason,
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
