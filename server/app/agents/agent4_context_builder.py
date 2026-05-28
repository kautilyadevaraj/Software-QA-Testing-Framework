"""Agent A4 — Context Builder (no LLM).

Assembles the full execution context for a test case by combining:
  - test steps (from DB via test_id)
  - DOM snapshot for the target page (HTML + accessibility tree + interactive elements)
  - **Phase-2 recorded selectors / steps / variants** (ground-truth locators)
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
import uuid
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select

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


def _serialize_recorded_steps(steps: list[ScenarioStep]) -> list[dict[str, Any]]:
    """Trim ORM rows to the fields A5 needs in its prompt."""
    out: list[dict[str, Any]] = []
    for s in steps[:_MAX_RECORDED_STEPS]:
        out.append({
            "step_index": s.step_index,
            "action": s.action_type,
            "selector": s.selector or "",
            "selector_candidates": list(s.selector_candidates or []),
            "value": s.value or "",
            "input_value_kind": s.input_value_kind or "",
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
    for s in recorded_steps:
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
    sel = (step.selector or "").replace("'", "\\'")
    val = step.value or ""

    if action == "navigate" and step.url:
        try:
            from urllib.parse import urlparse
            path = urlparse(step.url).path or "/"
        except Exception:
            path = "/"
        return f"  await page.goto(env('BASE_URL') + '{path}');"

    if action == "click" and sel:
        return f"  await page.locator('{sel}').click();"

    if action == "fill" and sel:
        # Heuristic credential masking — render env() placeholders for things
        # that look like emails / passwords so the few-shot doesn't leak the
        # captured credential value.
        if val and _CREDENTIAL_VALUE_RE.match(val):
            placeholder = "env('USER_PASSWORD')" if "pass" in (sel.lower() + val.lower()) else "env('USER_EMAIL')"
            return f"  await page.locator('{sel}').fill({placeholder});"
        safe_val = val.replace("'", "\\'")
        return f"  await page.locator('{sel}').fill('{safe_val}');"

    if action == "select" and sel:
        safe_val = val.replace("'", "\\'") if val else ""
        return f"  await page.locator('{sel}').selectOption({{ value: '{safe_val}' }});"

    return None


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
        'test("(synthesized from this project\'s Phase-2 recording)", async ({ page }) => {\n'
        '  const monitor = new NetworkMonitor(page);\n'
        f"{body}\n"
        '  expect(monitor.hasFailures()).toBe(false);\n'
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
    if (auth_mode or "").lower() != "login_flow":
        return None
    if not recorded_steps:
        return None
    first = recorded_steps[0]
    if first.action_type != "navigate" or not first.url:
        return None
    try:
        from urllib.parse import urlparse
        path = urlparse(first.url).path or "/"
    except Exception:
        path = "/"
    return path or "/"


def _resolve_target_page(
    declared: str,
    recorded_steps: list[ScenarioStep],
) -> tuple[str, str | None]:
    """Reconcile A3's declared `target_page` against Phase-2 recorded ground truth.

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
    first = recorded_steps[0]
    if first.action_type != "navigate" or not first.url:
        return declared, None
    try:
        from urllib.parse import urlparse
        recorded_path = urlparse(first.url).path or first.url
    except Exception:
        recorded_path = first.url
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
                .order_by(RecordingSession.created_at.desc())
                .limit(1)
            ).scalar_one_or_none()

        # Phase-2 recorded steps for this scenario (gold-standard selectors).
        recorded_steps: list[ScenarioStep] = []
        if latest_recording is not None:
            recorded_steps = list(
                db.execute(
                    select(ScenarioStep)
                    .where(ScenarioStep.recording_session_id == latest_recording.id)
                    .order_by(ScenarioStep.step_index)
                ).scalars()
            )

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
        tc_title = tc.title
        tc_steps = tc.steps
        tc_acceptance_criteria = tc.acceptance_criteria or []
        tc_assertion_evidence = tc.assertion_evidence or []
        tc_declared_target_page = tc.target_page
        # Reconcile A3's target_page against Phase-2 recorded ground truth.
        tc_target_page, override_reason = _resolve_target_page(
            tc_declared_target_page, recorded_steps,
        )
        if override_reason:
            logger.warning("agent4: target_page override test_id=%s — %s", test_id, override_reason)
        tc_depends_on = [str(d) for d in (tc.depends_on or [])]
        tc_auth_mode = tc.auth_mode or "authenticated"
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
