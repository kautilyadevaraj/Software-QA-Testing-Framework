"""Agent A4 — Context Builder (no LLM).

Assembles the full execution context for a test case by combining:
  - test steps (from DB via test_id)
  - DOM snapshot for the target page (HTML + accessibility tree + interactive elements)
  - **Phase-2 recorded selectors / steps / variants** (execution evidence)
  - ENV placeholder tokens (credentials)

Why Phase-2 enrichment lives here (and not in A5):
  - A4 is the single source of context for both A5 (generate) and A7 (retry)
  - Context is deterministic per (test_id, target_page) → cacheable in Redis later
  - A5 stays a thin LLM-call + post-processor; testing it stops requiring DB fixtures
  - Worker fan-out is simpler: one A4 call → many A5 attempts, all see the same context

Entry point: build_context(test_id, project_id) -> dict
"""
from __future__ import annotations

import logging
import re
import uuid
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.phase3 import TestCase
from app.models.scenario import (
    DiscoveredRoute,
    RecordedAssertionCandidate,
    RecordedRouteTransition,
    RecordingSession,
    RouteVariant,
    ScenarioStep,
)
from app.services import mcp_server

logger = logging.getLogger(__name__)

# How many recorded items we surface to A5. Caps prompt cost; recorded data is
# sorted by step_index / captured_at so the most relevant entries come first.
_MAX_RECORDED_STEPS = 40
_MAX_VARIANT_ELEMENTS = 30
_MAX_ROUTE_SNAPSHOTS = 8


_EXACT_HREF_SELECTOR_RE = re.compile(r"""(?P<prefix>\bhref\s*=\s*)(?P<quote>["'])(?P<href>[^"']+)(?P=quote)""")


def _route_pattern(path: str | None) -> str | None:
    """Generalize dynamic route ids while preserving static route structure."""
    raw = str(path or "").strip()
    if not raw:
        return None
    try:
        parsed = urlparse(raw)
        path_part = parsed.path or raw
    except Exception:
        path_part = raw
    pattern = re.sub(r"(?<=/)(?:\d+|[0-9a-f]{8,})(?=/|$)", "{id}", path_part, flags=re.IGNORECASE)
    return pattern if pattern != path_part else None


def _abstract_selector_hint(selector: str | None) -> str | None:
    """Return a safer selector hint for dynamic business-object selectors.

    Example: a[href="/records/2"] -> a[href*="/records/"]
    """
    raw = str(selector or "").strip()
    if not raw:
        return None
    if "nth-of-type" in raw:
        return None
    match = _EXACT_HREF_SELECTOR_RE.search(raw)
    if not match:
        return None
    href = match.group("href")
    pattern = _route_pattern(href)
    if not pattern:
        return None
    stable_prefix = pattern.split("{id}", 1)[0]
    if not stable_prefix or stable_prefix == "/":
        return None
    return _EXACT_HREF_SELECTOR_RE.sub(f"href*=\"{stable_prefix}\"", raw, count=1)


def _intent_hint_for_step(step: ScenarioStep, selector_hint: str | None = None) -> str | None:
    label = step.element_text or step.accessible_name or step.label
    if label:
        return str(label).strip()
    selector = step.selector or selector_hint or ""
    match = _EXACT_HREF_SELECTOR_RE.search(selector)
    if match:
        route = _route_pattern(match.group("href")) or match.group("href")
        words = re.sub(r"[^A-Za-z0-9]+", " ", route.replace("{id}", "")).strip()
        if words:
            suffix = "link" if (step.role or step.element_type or "").lower() in {"link", "a"} or selector.startswith("a") else "control"
            return f"{words} {suffix}".strip()
    return None


def _serialize_recorded_steps(steps: list[ScenarioStep]) -> list[dict[str, Any]]:
    """Trim ORM rows to the fields A5 needs in its prompt."""
    out: list[dict[str, Any]] = []
    for s in steps[:_MAX_RECORDED_STEPS]:
        semantic_context = s.semantic_context or {}
        field_identity = (
            semantic_context.get("field_identity")
            if isinstance(semantic_context, dict)
            else None
        )
        out.append({
            "step_index": s.step_index,
            "action": s.action_type,
            "selector": s.selector or "",
            "selector_candidates": list(s.selector_candidates or []),
            "selector_hint": _abstract_selector_hint(s.selector),
            "intent_hint": _intent_hint_for_step(s, _abstract_selector_hint(s.selector)),
            "selector_quality_reason": s.selector_quality_reason or "",
            "field_identity": field_identity or {},
            "value": s.value or "",
            "input_value_kind": s.input_value_kind or "",
            "input_type": s.input_type or "",
            "label": s.label or "",
            "element_text": s.element_text or "",
            "element_type": s.element_type or "",
            "accessible_name": s.accessible_name or "",
            "role": s.role or "",
            "url": s.url or "",
            "from_url": s.url_before or s.url or "",
            "to_url": s.url_after or "",
            "before_snapshot_id": str(s.route_variant_before_id) if s.route_variant_before_id else None,
            "after_snapshot_id": str(s.route_variant_after_id) if s.route_variant_after_id else None,
        })
    return out


def _non_noise_steps(steps: list[ScenarioStep]) -> list[ScenarioStep]:
    """Return only business-relevant recorded steps.

    Phase 2 stores ad/captcha/consent interactions for auditability, but Phase 3
    must not learn from them or replay them.
    """
    return [s for s in steps if not getattr(s, "is_noise", False)]


def _serialize_route_transitions(rows: list[RecordedRouteTransition]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows[:_MAX_RECORDED_STEPS]:
        out.append({
            "step_index": row.step_index,
            "from_url": row.from_url or "",
            "from_path": row.from_path or "",
            "to_url": row.to_url or "",
            "to_path": row.to_path or "",
            "action_type": row.action_type,
            "selector": row.selector or "",
            "element_text": row.element_text or "",
            "accessible_name": row.accessible_name or "",
            "transition_type": row.transition_type,
            "confidence": row.confidence,
            "before_snapshot_id": str(row.before_snapshot_id) if row.before_snapshot_id else None,
            "after_snapshot_id": str(row.after_snapshot_id) if row.after_snapshot_id else None,
        })
    return out


def _serialize_flow_pages(rows: list[RouteVariant]) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    for row in rows[:_MAX_ROUTE_SNAPSHOTS]:
        route = row.route
        pages.append({
            "snapshot_id": str(row.id),
            "snapshot_index": row.snapshot_index,
            "snapshot_kind": row.snapshot_kind,
            "path": route.path if route else "",
            "url": route.full_url if route else "",
            "title": route.page_title if route else "",
            "interactive_elements": list(row.interactive_elements or [])[:_MAX_VARIANT_ELEMENTS],
            "assertion_candidates": list(row.assertion_candidates or [])[:40],
        })
    return pages


def _serialize_assertion_candidates(rows: list[RecordedAssertionCandidate]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = str(row.snapshot_id)
        grouped.setdefault(key, []).append({
            "candidate_index": row.candidate_index,
            "kind": row.kind,
            "selector": row.selector,
            "text": row.text,
            "confidence": row.confidence,
        })
    return grouped


def _serialize_variant_elements(variant: RouteVariant | None) -> list[dict[str, Any]]:
    """Pick the latest RouteVariant for this scenario+route and surface its
    interactive_elements. These are richer than the static DiscoveredRoute
    snapshot because they were captured during an actual run-through."""
    if not variant or not variant.interactive_elements:
        return []
    return list(variant.interactive_elements)[:_MAX_VARIANT_ELEMENTS]


def _normalize_path_candidates(path: str) -> list[str]:
    """Return ordered candidate paths to try for snapshot lookup.

    A3 sometimes emits a target_page with a query string or trailing slash that
    doesn't match how Phase 1 stored it. We try the exact value first (cheap and
    correct in 95% of cases), then strip query/fragment, then drop trailing slash.
    Order matters: most specific → least specific.
    """
    if not path:
        return [path]
    seen: set[str] = set()
    out: list[str] = []

    def push(candidate: str) -> None:
        if candidate and candidate not in seen:
            out.append(candidate)
            seen.add(candidate)

    push(path)
    # Strip query / fragment.
    base = path.split("?", 1)[0].split("#", 1)[0]
    push(base)
    # Drop trailing slash unless it's the only character (root "/").
    if len(base) > 1 and base.endswith("/"):
        push(base.rstrip("/"))
    return out


def _fetch_snapshot_with_fallback(project_id: str, target_page: str) -> dict[str, Any]:
    """Try snapshot lookup against several normalized variants of target_page.

    Returns the first successful match. Behavior on total miss:
      - settings.a4_strict_snapshot=True  → raise ValueError so the test_id
        is routed to HUMAN_REVIEW. Use in production to surface Phase-1 gaps.
      - settings.a4_strict_snapshot=False → return an empty stub (legacy /
        dev mode). A5 still has recorded_steps to ground on.
    """
    from app.core.config import settings as _settings  # late import for tests
    last_exc: Exception | None = None
    candidates = _normalize_path_candidates(target_page)
    for candidate in candidates:
        try:
            snap = mcp_server.get_snapshot(project_id, candidate)
            if candidate != target_page:
                logger.info(
                    "agent4: snapshot fallback hit for '%s' → '%s'",
                    target_page, candidate,
                )
            return snap
        except ValueError as exc:
            last_exc = exc
            continue
    msg = f"DOM snapshot missing for '{target_page}' (tried {candidates}): {last_exc}"
    if _settings.a4_strict_snapshot:
        logger.error("agent4 (strict): %s", msg)
        raise ValueError(msg)
    logger.warning("agent4: %s", msg)
    return {
        "path": target_page,
        "html": "",
        "accessibility_tree": [],
        "interactive_elements": [],
    }


_FEW_SHOT_MAX_STEPS = 8

# Per-app `testIdAttribute` detection. Playwright's default is `data-testid`,
# but apps commonly use `data-test`, `data-cy` (Cypress holdovers),
# `data-qa`, `data-test-id` (Stripe), etc. When the LLM emits getByTestId(...)
# Playwright resolves it against whatever `testIdAttribute` is configured —
# wrong attribute = silent timeout. We scan recorded selectors and surface the
# dominant attribute so A5 emits a per-spec `test.use({ testIdAttribute })`.
_TEST_ID_ATTR_RE = __import__("re").compile(
    r"\[(data-(?:test(?:-?id)?|cy|qa|pw|automation-?id))\b"
)


def _detect_test_id_attribute(recorded_steps: list[ScenarioStep]) -> str | None:
    """Return the most-common test-id-style attribute used in recorded
    selectors, or None if none found / Playwright's default applies."""
    counts: dict[str, int] = {}
    for s in _non_noise_steps(recorded_steps):
        sel = s.selector or ""
        for m in _TEST_ID_ATTR_RE.finditer(sel):
            attr = m.group(1)
            counts[attr] = counts.get(attr, 0) + 1
    if not counts:
        return None
    # Tie-break: most frequent, then lexicographic for determinism.
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
# Heuristic: detect step values that look like credentials so we render env()
# placeholders rather than literal usernames/emails (Phase-2 may have captured
# the real login). Conservative — only matches obvious patterns.
_CREDENTIAL_VALUE_RE = __import__("re").compile(
    r"^(?:[\w.+-]+@[\w-]+\.[\w.-]+|.{8,}password.*)$",
    __import__("re").IGNORECASE,
)


def _render_few_shot_step(step: ScenarioStep) -> str | None:
    """Render a single recorded step into a single Playwright TS line.

    Returns None for action types we can't faithfully render (e.g. scroll,
    hover) — they're skipped from the few-shot rather than rendered as guesses.
    """
    action = step.action_type
    selector = _abstract_selector_hint(step.selector) or step.selector or ""
    sel = selector.replace("'", "\\'")
    val = step.value or ""

    if action == "navigate" and step.url:
        try:
            from urllib.parse import urlparse
            path = urlparse(step.url).path or "/"
        except Exception:
            path = "/"
        return f"  await page.goto(env('BASE_URL') + '{path}');"

    if action in {"click", "submit"} and sel:
        return f"  await page.locator('{sel}').click();"

    if action == "fill" and sel:
        # Heuristic credential masking — render env() placeholders for things
        # that look like emails / passwords so the few-shot doesn't leak the
        # captured credential value.
        value_kind = (step.input_value_kind or "").lower()
        if value_kind == "credential":
            placeholder = "env('TEST_PASSWORD')" if "pass" in (sel.lower() + (step.input_type or "").lower()) else "env('TEST_USERNAME')"
            return f"  await page.locator('{sel}').fill({placeholder});"
        if val and _CREDENTIAL_VALUE_RE.match(val):
            placeholder = "env('TEST_PASSWORD')" if "pass" in (sel.lower() + val.lower()) else "env('TEST_USERNAME')"
            return f"  await page.locator('{sel}').fill({placeholder});"
        safe_val = _few_shot_fill_value(step).replace("'", "\\'")
        return f"  await page.locator('{sel}').fill('{safe_val}');"

    if action == "select" and sel:
        safe_val = val.replace("'", "\\'") if val else ""
        return f"  await page.locator('{sel}').selectOption({{ value: '{safe_val}' }});"

    return None


def _few_shot_fill_value(step: ScenarioStep) -> str:
    """Use deterministic placeholder data in examples instead of replay data."""
    val = step.value or ""
    value_kind = (step.input_value_kind or "").lower()
    if value_kind == "credential":
        return val
    if (step.input_type or "").lower() == "number":
        return "1"
    field_text = " ".join(
        str(value or "")
        for value in (
            step.selector,
            step.label,
            step.element_text,
            step.accessible_name,
            step.input_type,
            (step.semantic_context or {}).get("field_identity", {}) if isinstance(step.semantic_context, dict) else "",
        )
    ).lower()
    if any(term in field_text for term in ("quantity", "qty", "count", "number")):
        return "1"
    if any(term in field_text for term in ("postal", "postcode", "zip", "pin", "code")):
        return settings.phase3_test_data_postal_code
    if any(term in field_text for term in ("email", "mail")):
        return settings.phase3_test_data_email
    if any(term in field_text for term in ("search", "keyword", "query")):
        return settings.phase3_test_data_search
    if any(term in field_text for term in ("first", "last", "name", "customer", "user")):
        return settings.phase3_test_data_name
    if any(term in field_text for term in ("phone", "mobile", "contact")):
        return settings.phase3_test_data_phone
    if val and len(val) <= 3 and val.isdigit():
        return "1"
    return settings.phase3_test_data_search


def _synthesize_few_shot(project_id_uuid: uuid.UUID, db) -> str | None:
    """Build a structural few-shot example from THIS project's recordings.

    Falls back to None when the project has no recorded steps yet (caller
    keeps the static example baked into the prompt). Replacing the static
    example with a per-project one teaches the LLM the actual selector
    idiom of THIS app — eliminating static example bias entirely.
    """
    rows = list(
        db.execute(
            select(ScenarioStep)
            .where(ScenarioStep.project_id == project_id_uuid)
            .where(ScenarioStep.is_noise.is_(False))
            .order_by(ScenarioStep.scenario_id, ScenarioStep.step_index)
            .limit(_FEW_SHOT_MAX_STEPS)
        ).scalars()
    )
    if not rows:
        return None

    rendered: list[str] = []
    for s in rows:
        line = _render_few_shot_step(s)
        if line:
            rendered.append(line)
    if not rendered:
        return None

    body = "\n".join(rendered)
    return (
        'test("(structural evidence from this project\'s Phase-2 recording; do not replay exact business objects)", async ({ page }, testInfo) => {\n'
        '  const monitor = new NetworkMonitor(page);\n'
        f"{body}\n"
        '  await testInfo.attach(\'network_logs\', { body: JSON.stringify(monitor.failures, null, 2), contentType: \'application/json\' });\n'
        '  expect(monitor.failures, JSON.stringify(monitor.failures, null, 2)).toEqual([]);\n'
        '});'
    )


def _resolve_auth_login_path(
    auth_mode: str,
    recorded_steps: list[ScenarioStep],
) -> str | None:
    """For login-flow tests, derive the canonical login-page path from Phase-2.

    Phase-2 captured the actual tester's login sequence. recorded_steps[0]
    of any login_flow scenario is by definition a navigate to the app's login
    page. We surface its path so A5/A7 can ground their `goto` target on the
    real recording — not a hardcoded `/`, `/login`, or post-login route list.

    Returns the path component (e.g. '/', '/login', '/sign-in', '/users/auth')
    or None when this test is not a login flow OR no recording is available.
    """
    if not recorded_steps:
        return None
    first = recorded_steps[0]
    start_url = first.url_before or first.url
    if not start_url:
        return None
    try:
        from urllib.parse import urlparse
        path = urlparse(start_url).path or "/"
    except Exception:
        path = "/"
    return path or "/"


def _resolve_target_page(
    declared: str,
    recorded_steps: list[ScenarioStep],
    auth_mode: str = "",
) -> tuple[str, str | None]:
    """Reconcile A3's declared `target_page` against Phase-2 recorded evidence.

    A3 is an LLM that infers target_page from the test title; it gets this wrong
    in surprising ways. Example: a workflow title can bias the model toward the
    destination page instead of the actual starting page, causing A5 to navigate
    past required setup steps and time out on missing selectors.

    Phase-2 recorded the actual tester actions. recorded_steps[0] is almost
    always a `navigate` action whose URL is the canonical starting page.

    Returns: (resolved_target_page, override_reason or None).
    The reason is non-None when we replaced A3's value; the caller logs it.
    """
    if not recorded_steps:
        return declared, None
    if (auth_mode or "").lower() == "authenticated":
        post_auth_path = _first_authenticated_route_path(recorded_steps)
        if post_auth_path:
            declared_base = (declared or "").split("?", 1)[0].split("#", 1)[0]
            if post_auth_path != declared_base:
                return post_auth_path, (
                    f"A3 declared target_page={declared!r}; authenticated test starts "
                    f"from first post-auth recorded route {post_auth_path!r}"
                )

    first = recorded_steps[0]
    start_url = first.url_before or first.url
    if not start_url:
        return declared, None
    try:
        from urllib.parse import urlparse
        recorded_path = urlparse(start_url).path or start_url
    except Exception:
        recorded_path = start_url
    if not recorded_path:
        return declared, None
    # Strip query / fragment for comparison so equivalent route variants do not
    # trigger a spurious override.
    declared_base = (declared or "").split("?", 1)[0].split("#", 1)[0]
    if recorded_path == declared_base:
        return declared, None
    return recorded_path, (
        f"A3 declared target_page={declared!r} but Phase-2 recorded_steps[0] "
        f"navigates to {recorded_path!r}; trusting recording"
    )


def _first_authenticated_route_path(recorded_steps: list[ScenarioStep]) -> str | None:
    if not recorded_steps:
        return None
    first_url = recorded_steps[0].url_before or recorded_steps[0].url
    try:
        first_path = urlparse(first_url or "").path or "/"
    except Exception:
        first_path = first_url or ""

    for step in recorded_steps:
        after = step.url_after or step.url
        if not after:
            continue
        try:
            after_path = urlparse(after).path or "/"
        except Exception:
            after_path = after
        if not after_path or after_path == first_path:
            continue
        if _scenario_step_is_auth_setup_control(step):
            return after_path

    for step in recorded_steps:
        if _scenario_step_is_auth_setup_control(step):
            continue
        candidate = step.url_before or step.url or step.url_after
        if not candidate:
            continue
        try:
            candidate_path = urlparse(candidate).path or "/"
        except Exception:
            candidate_path = candidate
        if candidate_path and candidate_path != first_path:
            return candidate_path
    return None


def _scenario_step_is_auth_setup_control(step: ScenarioStep) -> bool:
    control_text = " ".join(
        str(value or "")
        for value in (
            step.selector,
            step.playwright_locator,
            step.element_text,
            step.accessible_name,
            step.role,
            step.label,
            step.value,
        )
    ).lower()
    return any(
        term in control_text
        for term in (
            "login",
            "log in",
            "sign in",
            "signin",
            "logout",
            "log out",
            "sign out",
            "signout",
            "username",
            "password",
            "register",
            "signup",
        )
    )


def _build_route_map(steps: list[ScenarioStep]) -> dict[str, str]:
    """Compact path → element_text mapping so A5 understands page transitions.

    Example: {'/records': 'Records', '/records/new': 'Create'}
    """
    route_map: dict[str, str] = {}
    for s in steps:
        candidate_url = s.url_after or s.url
        if candidate_url:
            try:
                from urllib.parse import urlparse
                path = urlparse(candidate_url).path or candidate_url
            except Exception:
                path = candidate_url
            if path and path not in route_map:
                route_map[path] = s.element_text or ""
    return route_map


def _build_route_patterns(route_map: dict[str, str]) -> dict[str, str]:
    """Expose dynamic route shapes separately from exact recorded routes."""
    patterns: dict[str, str] = {}
    for path, label in route_map.items():
        pattern = _route_pattern(path)
        if pattern and pattern not in patterns:
            patterns[pattern] = label
    return patterns


def _path_from_url(url: str | None) -> str | None:
    if not url:
        return None
    try:
        parsed = urlparse(url)
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        return path
    except Exception:
        return url or None


def _recorded_route_paths(steps: list[ScenarioStep], target_page: str) -> list[str]:
    """Return ordered routes visited by the recording, not just navigate steps.

    Multi-page tests often begin at login but interact later with elements on a
    post-login page. A5 needs those route DOM snapshots to refine weak recorded
    selectors such as `select` into stable app-specific selectors.
    """
    seen: set[str] = set()
    out: list[str] = []

    def push(path: str | None) -> None:
        if not path:
            return
        base = path.split("#", 1)[0]
        if base and base not in seen:
            out.append(base)
            seen.add(base)

    push(target_page)
    for step in steps:
        push(_path_from_url(step.url))
        push(_path_from_url(step.url_before))
        push(_path_from_url(step.url_after))
        if len(out) >= _MAX_ROUTE_SNAPSHOTS:
            break
    return out


def _build_route_snapshots(project_id: str, paths: list[str]) -> dict[str, dict[str, Any]]:
    snapshots: dict[str, dict[str, Any]] = {}
    for path in paths[:_MAX_ROUTE_SNAPSHOTS]:
        try:
            snap = _fetch_snapshot_with_fallback(project_id, path)
        except Exception as exc:
            logger.warning("agent4: route snapshot skipped path=%s error=%s", path, exc)
            continue
        snapshots[path] = {
            "path": snap.get("path") or path,
            "html": snap.get("html") or "",
            "interactive_elements": list(snap.get("interactive_elements") or [])[:_MAX_VARIANT_ELEMENTS],
        }
    return snapshots


async def build_context(test_id: str, project_id: str) -> dict[str, Any]:
    """Return a ContextObject dict for Agent A5 to consume.

    Raises ValueError if test_case or DOM snapshot is not found.

    Returns:
        {
            "test_id": str,
            "title": str,
            "steps": list[str],
            "acceptance_criteria": list[str],
            "assertion_evidence": list[dict],
            "target_page": str,
            "dom": {...},
            "env_placeholders": {...},
            "depends_on": list[str],
            "auth_mode": str,
            "credential_id": str | None,
            "credential_role": str | None,
            # ── Phase-2 enrichment ─────────────────────────────────────────
            "recorded_steps": [{step_index, action, selector, value,
                                element_text, element_type, url}, ...],
            "recorded_variant_elements": [{...}, ...],
            "route_map": {path: link_text, ...},
        }
    """
    project_uuid = uuid.UUID(project_id)

    with SessionLocal() as db:
        tc = db.get(TestCase, uuid.UUID(test_id))
        if not tc:
            raise ValueError(f"TestCase not found: test_id={test_id}")

        latest_recording: RecordingSession | None = None
        if tc.hls_id is not None:
            latest_recording = db.execute(
                select(RecordingSession)
                .where(RecordingSession.scenario_id == tc.hls_id)
                .where(RecordingSession.status == "completed")
                .order_by(RecordingSession.created_at.desc())
                .limit(1)
            ).scalar_one_or_none()

        # Phase-2 recorded steps for this scenario. These are evidence of real
        # UI behavior, not a replay contract for A5/A7.
        recorded_steps: list[ScenarioStep] = []
        recorded_step_indexes: set[int] = set()
        if latest_recording is not None:
            recorded_steps = list(
                db.execute(
                    select(ScenarioStep)
                    .where(ScenarioStep.recording_session_id == latest_recording.id)
                    .where(ScenarioStep.is_noise.is_(False))
                    .order_by(ScenarioStep.step_index)
                ).scalars()
            )
            recorded_step_indexes = {s.step_index for s in recorded_steps}

        recorded_transitions: list[RecordedRouteTransition] = []
        flow_pages: list[RouteVariant] = []
        recorded_assertions: list[RecordedAssertionCandidate] = []
        if latest_recording is not None:
            recorded_transitions = list(
                db.execute(
                    select(RecordedRouteTransition)
                    .where(RecordedRouteTransition.recording_id == latest_recording.id)
                    .order_by(RecordedRouteTransition.step_index)
                ).scalars()
            )
            # Transitions share ScenarioStep.step_index with their source
            # action. Since Phase 2 step indexes are sequential per recording,
            # filtering transitions by the retained non-noise step indexes
            # excludes transitions caused only by ad/captcha/consent noise.
            if recorded_step_indexes:
                recorded_transitions = [
                    row for row in recorded_transitions
                    if row.step_index in recorded_step_indexes
                ]
            flow_pages = list(
                db.execute(
                    select(RouteVariant)
                    .where(RouteVariant.recording_session_id == latest_recording.id)
                    .order_by(RouteVariant.snapshot_index.asc().nullslast(), RouteVariant.captured_at.asc())
                ).scalars()
            )
            snapshot_ids = [p.id for p in flow_pages]
            if snapshot_ids:
                recorded_assertions = list(
                    db.execute(
                        select(RecordedAssertionCandidate)
                        .where(RecordedAssertionCandidate.snapshot_id.in_(snapshot_ids))
                        .order_by(RecordedAssertionCandidate.snapshot_id, RecordedAssertionCandidate.candidate_index)
                    ).scalars()
                )

        # Latest RouteVariant for the (scenario, target_page) pair, if any.
        # Falls back gracefully when Phase-2 didn't capture this page.
        latest_variant: RouteVariant | None = None
        if tc.hls_id is not None:
            route_row = db.execute(
                select(DiscoveredRoute).where(
                    DiscoveredRoute.project_id == project_uuid,
                    DiscoveredRoute.path == tc.target_page,
                )
            ).scalar_one_or_none()
            if route_row is not None:
                latest_variant = db.execute(
                    select(RouteVariant)
                    .where(
                        RouteVariant.recording_session_id == latest_recording.id if latest_recording else RouteVariant.scenario_id == tc.hls_id,
                        RouteVariant.route_id == route_row.id,
                    )
                    .order_by(RouteVariant.snapshot_index.desc().nullslast(), RouteVariant.captured_at.desc())
                    .limit(1)
                ).scalar_one_or_none()

        # Snapshot fields we need from the ORM rows BEFORE the session closes.
        recorded_steps_payload = _serialize_recorded_steps(recorded_steps)
        route_transitions_payload = _serialize_route_transitions(recorded_transitions)
        flow_pages_payload = _serialize_flow_pages(flow_pages)
        assertions_by_snapshot_payload = _serialize_assertion_candidates(recorded_assertions)
        variant_elements_payload = _serialize_variant_elements(latest_variant)
        route_map_payload = _build_route_map(recorded_steps)
        route_patterns_payload = _build_route_patterns(route_map_payload)
        tc_title = tc.title
        tc_steps = tc.steps
        tc_acceptance_criteria = tc.acceptance_criteria or []
        tc_assertion_evidence = tc.assertion_evidence or []
        tc_declared_target_page = tc.target_page
        tc_auth_mode = tc.auth_mode or "authenticated"
        # Reconcile A3's target_page against Phase-2 recorded evidence.
        tc_target_page, override_reason = _resolve_target_page(
            tc_declared_target_page, recorded_steps, tc_auth_mode,
        )
        if override_reason:
            logger.warning("agent4: target_page override test_id=%s — %s", test_id, override_reason)
        tc_depends_on = [str(d) for d in (tc.depends_on or [])]
        tc_credential_id = str(tc.credential_id) if tc.credential_id else None
        tc_credential_role = tc.credential_role
        # Phase-2 derived login URL (replaces hardcoded `/` assumption).
        auth_login_path = _resolve_auth_login_path(tc_auth_mode, recorded_steps)
        # Per-project few-shot synthesised from Phase-2 recordings. The LLM
        # picks up THIS app's selector idiom rather than a baked-in example.
        # None when the project has no recordings yet.
        few_shot_example = _synthesize_few_shot(project_uuid, db)
        # Per-app testIdAttribute (e.g. 'data-test', 'data-cy'). None means
        # rely on Playwright's default `data-testid`.
        test_id_attribute = _detect_test_id_attribute(recorded_steps)

    # 2. DOM snapshot (HTML already minified by mcp_server.get_snapshot)
    dom = _fetch_snapshot_with_fallback(project_id, tc_target_page)
    route_snapshots = _build_route_snapshots(
        project_id,
        _recorded_route_paths(recorded_steps, tc_target_page),
    )

    # 3. ENV placeholders
    env_placeholders = mcp_server.get_placeholders()

    context: dict[str, Any] = {
        "test_id": test_id,
        # project_id is plumbed through so A5/A7 can write scripts under
        # tests/generated/<project_id>/<run_id>/ for multi-tenant isolation.
        "project_id": project_id,
        "title": tc_title,
        "steps": tc_steps,
        "acceptance_criteria": tc_acceptance_criteria,
        "assertion_evidence": tc_assertion_evidence,
        "target_page": tc_target_page,
        "dom": dom,
        "env_placeholders": env_placeholders,
        "depends_on": tc_depends_on,
        "auth_mode": tc_auth_mode,
        "credential_id": tc_credential_id,
        "credential_role": tc_credential_role,
        # Phase-2 derived login page path; None when not a login flow or when
        # Phase-2 has no recording. A5/A7 use this to ground login goto targets
        # for THIS app instead of a hardcoded `/` (which only works for apps
        # whose login form lives at the root).
        "auth_login_path": auth_login_path,
        # If non-empty, A5/A7 prompts replace their static structural example
        # with this app-specific one rendered from real Phase-2 recordings.
        "few_shot_example": few_shot_example,
        # When non-None, A5 emits `test.use({ testIdAttribute })` so the
        # LLM's getByTestId(...) calls resolve against this app's actual
        # attribute instead of Playwright's `data-testid` default.
        "test_id_attribute": test_id_attribute,
        # Phase-2 enrichment — see module docstring.
        "recorded_steps": recorded_steps_payload,
        "recorded_route_transitions": route_transitions_payload,
        "recording_flow": {
            "recording_id": str(latest_recording.id) if latest_recording else None,
            "hls_id": str(tc.hls_id) if tc.hls_id else None,
            "pages": flow_pages_payload,
            "actions": recorded_steps_payload,
            "route_transitions": route_transitions_payload,
            "assertion_candidates_by_snapshot": assertions_by_snapshot_payload,
        },
        "recorded_variant_elements": variant_elements_payload,
        "route_map": route_map_payload,
        "route_patterns": route_patterns_payload,
        "route_snapshots": route_snapshots,
    }

    logger.info(
        "agent4: built context test_id=%s page=%s recorded_steps=%d transitions=%d flow_pages=%d variant_elements=%d routes=%d route_snapshots=%d",
        test_id, tc_target_page,
        len(recorded_steps_payload),
        len(route_transitions_payload),
        len(flow_pages_payload),
        len(variant_elements_payload),
        len(route_map_payload),
        len(route_snapshots),
    )
    return context
