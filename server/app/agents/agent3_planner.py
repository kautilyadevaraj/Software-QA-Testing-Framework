"""Agent A3 — Planner Agent.

Decomposes a High-Level Scenario (HLS) recorded in Phase 2 into concrete
test_case rows using the configured LLM. Each test case carries:
  - `tc_number` (TC-001…) for Jira RTM traceability
  - `hls_id` so the UI can group test cases by parent scenario
  - `acceptance_criteria` so reviewers know the pass conditions
The approval UI reads structured JSON; X-Ray CSV export is handled separately.

Entry point:
    plan(htc_title, htc_description, pages, project_id,
         hls_id, recorded_steps, tc_sequence_start) -> list[str]
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select

from app.agents.scenario_common import build_text_batches, scroll_chunks
from app.db.session import SessionLocal
from app.models.project import CredentialProfile
from app.services import mcp_server
from app.utils.llm import call_llm

logger = logging.getLogger(__name__)

_MAX_LLM_RETRIES = 3
_BRD_CATEGORIES = ["brd", "fsd", "wbs", "assumptions", "assumption"]
_BARE_TAG_SELECTORS = {"a", "button", "div", "span", "input", "select", "form", "ul", "li", "p", "img"}
_FIELD_SELECTOR_HINTS = ("user", "password", "email", "name", "postal", "zip", "phone", "address", "field", "input")
_HTML_ATTR_RE = re.compile(
    r"""(?P<name>[:A-Za-z_][\w:.-]*)(?:\s*=\s*(?P<quote>["'])(?P<quoted>.*?)(?P=quote)|\s*=\s*(?P<bare>[^\s"'=<>`]+))?"""
)
_STABLE_ATTR_PRIORITY = (
    "data-testid",
    "data-test",
    "data-cy",
    "data-qa",
    "data-pw",
    "data-test-id",
    "id",
    "name",
    "aria-label",
    "placeholder",
)
_GRANULARITY_RETRY_PROMPT = """

IMPORTANT CORRECTION:
Your previous answer was too granular or reduced the scenario to prerequisite
login/setup actions. Regenerate the JSON array using complete QA validation
flows. For a broad scenario that combines authentication with another business
workflow, one test case must include authentication + the business action +
assertions in the same steps. Do not return a standalone positive "Login" test
unless the HLS is only about authentication.
Keep every step grounded in the provided pages and recorded actions.
"""

_XRAY_METADATA_PROMPT = """You are Agent A3, the automation test-case planning specialist.

Your job in this mode: enrich the EXISTING automation test cases with X-Ray
CSV metadata using BRD/FSD/WBS/assumption evidence. Do not create new test
cases. Do not remove test cases. Do not rename test cases.

Return ONLY a raw JSON array. Each item must contain:
- title: exact title from the provided automation test cases
- labels: one clean CSV label string such as Positive, Negative, Functional
- requirement: requirement/story key if present in source, otherwise empty
- priority: High, Medium, or Low
- pre_condition_data: human-readable setup/test data without secrets

Rules:
- Use only supplied document chunks and HLS guide as evidence.
- Never output raw passwords, secrets, tokens, or credential values.
- If source mentions credentials, write "Valid user credentials are available"
  or "Locked-out user credentials are available".
- Keep metadata compatible with Jira/X-Ray CSV import.
- Enrich only titles listed under Existing automation test cases.

Completed HLS coverage guide:
{hls_context}

Existing automation test cases:
{test_case_context}

Document chunks:
{document_text}
"""

_ASSERTION_EVIDENCE_PROMPT = """You are Agent A3b, an assertion-evidence extractor.

Your job: extract observable assertion evidence for ONE already-created QA test
case. Do NOT create or modify test cases. Do NOT invent expected text,
selectors, pages, counts, statuses, or business outcomes.

Return ONLY a raw JSON array. No markdown. Each item must contain:
- kind: one of ui_text, element_visible, element_absent, url_match,
  error_message, attribute_check, count_check, navigation
- outcome: short human-readable result to prove
- source: short source label such as "acceptance_criteria", "BRD/HLS", or "recording"
- source_text: exact text copied from the supplied testcase/HLS/docs/recording
- observable_hint: exact visible text, selector, route, count, or null
- confidence: number from 0.0 to 1.0
- grounding: one of doc, dom, acceptance_criteria, recording, inferred

Rules:
- Use testcase acceptance criteria first when they describe observable results.
- Use document/HLS text only when it directly supports the testcase.
- Use recorded selectors/routes only as observable hints, not as new behavior.
- Use navigation only as supporting evidence; it must not be the only evidence
  when the testcase expects visible UI confirmation.
- If the expected result is vague, output confidence below 0.5 and
  observable_hint null.
- If no grounded evidence exists, return [].

Testcase:
Title: {title}
Target Page: {target_page}
Steps:
{steps}
Acceptance Criteria:
{acceptance_criteria}

HLS:
{hls_context}

Document/BRD context:
{document_context}

Recorded evidence:
{recorded_context}
"""

_ASSERTION_EVIDENCE_KINDS = {
    "ui_text",
    "element_visible",
    "element_absent",
    "url_match",
    "error_message",
    "attribute_check",
    "count_check",
    "navigation",
}
_ASSERTION_EVIDENCE_GROUNDING = {
    "doc",
    "dom",
    "acceptance_criteria",
    "recording",
    "inferred",
}

# ── Prompt ────────────────────────────────────────────────────────────────────

_PLAN_PROMPT = """You are Agent A3, a senior QA test planning specialist.

Your job: turn one High-Level Scenario (HLS) from Phase 2 into concrete,
executable QA test cases that a Playwright automation script can run directly.

Definition:
- A test case is ONE complete business validation flow, not one recorded click,
  not one prerequisite action, and not necessarily one page.
- Prerequisite actions belong inside the same test case when they are needed to
  validate the business behavior. If the scenario combines authentication with
  a business workflow, create one test case that includes authentication plus
  that workflow and its assertions. Do NOT split setup from the actual business
  validation unless the HLS is specifically about setup/authentication behavior.
- Recorded tester actions are grounding evidence for available flows, URLs, and
  selectors. They are NOT the final test-case list.
- Real-world automation pattern: test cases are independent by default. Shared
  setup such as authentication, navigation, or selecting a business object
  belongs inside each test case.
- Use domain vocabulary only from the HLS, descriptions, discovered pages,
  recorded actions, DOM/app context, or Swagger. Do not borrow nouns from
  examples.

═══ RULES ═══
1. Each test case may span multiple discovered/recorded pages when that is
   required to validate one business flow. `target_page` is the starting or
   primary page, not the only page.
2. Steps must be concise and action-oriented so a Playwright script can execute
   them without ambiguity.
   GOOD:  "click the Login button"
   BAD:   "the user should be able to log in"
3. Use ENV placeholder tokens for ALL sensitive values — NEVER hardcode:
     {{TEST_USERNAME}}, {{TEST_PASSWORD}}, {{BASE_URL}}
4. Do NOT invent pages not in the provided pages list.
5. If recorded tester actions are provided, use them as evidence for what
   the app supports. Generate QA validations from the HLS/BRD intent, but keep
   every navigation/action/assertion grounded in recorded actions, discovered
   pages, app context, DOM evidence, or Swagger capability.
6. `depends_on` should almost always be []. Do NOT chain normal UI/business
   flows together. Only use `depends_on` when a later test truly requires
   unique data created by an earlier test and cannot create/setup that data
   itself (rare lifecycle cases such as create → edit same record → delete).
7. `acceptance_criteria`: 2–4 short, verifiable pass conditions.
   Think: what would a QA manager check to sign off this test case?
8. Login is a standalone test case only for auth-specific validations such as
   invalid password, empty credentials, locked user, logout, or password reset.
   Otherwise, include login as setup steps inside the broader business test.
9. Do not create a separate dependent test case for the final assertion of a
   previous flow. Merge the final verification into the complete business-flow
   test case.
10. Be concise — steps should be 5–10 words. Acceptance criteria should be 5–12
   words. Return at most 4 test cases per HLS. Extra verbosity wastes tokens and
   produces no additional coverage value.
10b. Prefer fewer, fuller QA flows over many tiny replayed actions.
11. Negative validation cases must be grounded. Only use domain-specific
   "invalid value" data when the HLS, recordings, DOM/app context, BRD, or
   Swagger explicitly shows that validation rule. If a form-validation negative
   case is useful but the exact invalid-value rule is not evidenced, prefer a
   required-field case: leave one or more recorded required fields empty, submit,
   and assert the validation feedback.
   For authentication, do NOT invent "invalid email/password" data. Use only
   uploaded/documented credential profiles such as locked/blocked users, or use
   required-field validation when no negative credential profile is available.
12. Return ONLY a raw JSON array — no markdown fences, no explanation.
13. Do not compress away intermediate recorded navigation or control-revealing
   actions required to make a later step possible. If the recording shows a
   user must open an intermediate section, detail view, list, review screen, or work area before a later
   button, form, or confirmation is available, include that bridge step using
   the recorded evidence. This rule is app-neutral; do not assume a specific
   domain, module, or page name.
14. Do not output low-level focus/noise actions as test steps. If a field is
   filled, do not also add a separate click/focus step for the same field.
15. Do not output bare tag selectors such as `click div`, `click a`,
   `click button`, or `select select`. Use the stable recorded/DOM selector or
   a business-readable action grounded in evidence.
16. Phase-2 recording is evidence, not replay. Do not copy exact recorded
   business objects, route ids, row ids, record ids, employee ids, search
   terms, quantities, or detail URLs unless the HLS/BRD explicitly names that
   exact object/value.
   GOOD for generic intent: "click a details link for an available record",
   "select an available item", "fill quantity with a valid quantity",
   "search using a valid representative keyword".
   BAD for generic intent: "click record 2", "navigate to /details/2",
   "fill quantity with 2" when 2 only came from the recording.
17. Stable controls may keep exact selectors when grounded: login fields,
   submit/search buttons, menu links, workflow navigation, and primary action buttons. Dynamic
   business objects should be described by intent unless explicitly named.
18. Every item must set `auth_mode` explicitly:
   - "authenticated": credentials/login are setup for a non-auth business flow.
     Include login steps using {{TEST_USERNAME}}/{{TEST_PASSWORD}} when the app
     requires a session.
   - "login_flow": login/logout/register/reset/credential behavior itself is
     under test.
   - "anonymous": no credentials/session are needed.

Document usage:
- Requirement/document context is business evidence, not an automation script.
- Use it to strengthen titles, acceptance criteria, documented validation rules,
  requirement-aware assertions, priorities of coverage, and edge cases.
- Do not create BRD-only tests that cannot be executed through the discovered
  pages, recorded actions, DOM/app context, or Swagger capability.
- If document context conflicts with app evidence, prefer executable app
  evidence and keep the testcase automatable.

═══ OUTPUT SCHEMA ═══
[
  {{
    "title": "short descriptive title (max 8 words)",
    "steps": [
      "action description for step 1",
      "action description for step 2"
    ],
    "acceptance_criteria": [
      "verifiable pass condition 1",
      "verifiable pass condition 2"
    ],
    "auth_mode": "authenticated | login_flow | anonymous",
    "depends_on": [],
    "target_page": "/exact/page/path"
  }}
]

═══ STRUCTURAL PATTERN (APP-NEUTRAL) ═══
Use this as a shape guide only. Replace "core workflow", "business object",
and "expected result" with terms found in the HLS/recordings/app context.

[
  {{
    "title": "Complete core workflow and verify result",
    "steps": [
      "navigate to {{BASE_URL}}<recorded-start-path>",
      "authenticate if this workflow requires it",
      "perform the primary business action described by the HLS",
      "assert the expected result, confirmation, or state change is visible"
    ],
    "acceptance_criteria": [
      "User reaches the expected workflow state",
      "Expected result or state change is visible",
      "No application or network error is shown"
    ],
    "auth_mode": "authenticated",
    "depends_on": [],
    "target_page": "<recorded-start-path>"
  }},
  {{
    "title": "Required validation prevents incomplete submission",
    "steps": [
      "navigate to {{BASE_URL}}<recorded-start-path>",
      "leave one or more recorded required workflow fields empty",
      "submit the workflow form or action",
      "assert the validation message is visible",
      "assert the incomplete workflow is not completed"
    ],
    "acceptance_criteria": [
      "Missing required input is rejected",
      "Clear validation feedback is shown",
      "No incomplete record or state transition is created"
    ],
    "auth_mode": "authenticated",
    "depends_on": [],
    "target_page": "<recorded-start-path>"
  }}
]

═══ YOUR TASK ═══
HLS Title: {htc_title}
HLS Description: {htc_description}

Available pages:
{pages_list}
{document_context_section}
{recorded_steps_section}"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _format_recorded_steps(recorded_steps: list[dict[str, Any]]) -> str:
    if not recorded_steps:
        return ""
    capped = recorded_steps[:20]
    lines  = ["\nRecorded tester actions (grounding evidence — do not copy 1:1):"]
    for i, step in enumerate(capped, 1):
        action   = step.get("action_type", "unknown")
        url      = step.get("url") or ""
        selector = step.get("selector") or ""
        value    = step.get("value") or ""
        parts    = [f"  {i}. {action}"]
        if url:      parts.append(f"url: {url}")
        if selector: parts.append(f"selector: {selector}")
        if value:    parts.append(f'value: "{value}"')
        lines.append("  ".join(parts))
    if len(recorded_steps) > 50:
        lines.append(f"  ... ({len(recorded_steps) - 50} more steps truncated)")
    return "\n".join(lines)


def _path_from_recorded_value(value: str) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = urlparse(raw)
        path = parsed.path or ""
        if parsed.query:
            path = f"{path}?{parsed.query}"
        return path or None
    except Exception:
        return None


def _looks_recording_specific_selector(selector: str) -> bool:
    lower = str(selector or "").lower()
    if not lower:
        return False
    if "nth-of-type" in lower:
        return True
    return bool(re.search(r"""\bhref\s*=\s*['"][^'"]*/(?:\d+|[0-9a-f]{8,})(?:[/?#'"]|$)""", lower))


def _looks_dynamic_route(path: str) -> bool:
    return bool(re.search(r"(?<=/)(?:\d+|[0-9a-f]{8,})(?=/|$)", str(path or ""), re.IGNORECASE))


def _is_sensitive_or_generic_recorded_value(value: str) -> bool:
    lower = str(value or "").strip().lower()
    if not lower:
        return True
    if lower.startswith("{") and lower.endswith("}"):
        return True
    if "password" in lower or "secret" in lower or "token" in lower or "@" in lower:
        return True
    if lower in {"test", "testing", "sample", "dummy", "value", "yes", "no", "on", "off"}:
        return True
    return False


def _recording_leakage_warnings(
    item: dict[str, Any],
    recorded_steps: list[dict[str, Any]] | None,
    explicit_evidence_text: str,
) -> list[str]:
    """Flag likely replay leakage without rewriting the testcase."""
    if not recorded_steps:
        return []
    item_text = _text_from_item(item)
    evidence = str(explicit_evidence_text or "").lower()
    warnings: list[str] = []

    for recorded in recorded_steps:
        selector = str(recorded.get("selector") or "").strip()
        if selector and _looks_recording_specific_selector(selector) and selector.lower() in item_text:
            if selector.lower() not in evidence:
                warnings.append(f"recorded-specific selector {selector!r}")

        for key in ("url", "url_before", "url_after", "from_url", "to_url"):
            path = _path_from_recorded_value(str(recorded.get(key) or ""))
            if path and _looks_dynamic_route(path) and path.lower() in item_text and path.lower() not in evidence:
                warnings.append(f"recorded-specific route {path!r}")

        value = str(recorded.get("value") or "").strip()
        if (
            value
            and not _is_sensitive_or_generic_recorded_value(value)
            and len(value) <= 40
            and value.lower() in item_text
            and value.lower() not in evidence
        ):
            warnings.append(f"recorded-specific value {value!r}")

    return list(dict.fromkeys(warnings))


_DOC_CONTEXT_STOP_WORDS = {
    "a", "an", "and", "are", "as", "be", "by", "can", "for", "from", "in",
    "is", "it", "of", "on", "or", "shall", "should", "the", "to", "user",
    "with",
}


def _context_tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9_]+", text.lower())
        if len(token) > 2 and token not in _DOC_CONTEXT_STOP_WORDS
    }


def _format_planning_document_context(
    project_id: str,
    hls_title: str,
    hls_description: str,
) -> str:
    """Fetch compact BRD/FSD/WBS context for one HLS planning prompt."""
    try:
        chunks = scroll_chunks(project_id, _BRD_CATEGORIES)
    except Exception as exc:
        logger.warning(
            "agent3: document context unavailable for project_id=%s hls=%r: %s",
            project_id,
            hls_title,
            exc,
        )
        return (
            "\nRequirement/document context from BRD/FSD/WBS/assumptions:\n"
            "  (unavailable; continue using HLS, pages, and recorded app evidence)"
        )

    query_tokens = _context_tokens(f"{hls_title} {hls_description}")
    ranked: list[tuple[int, int, str]] = []
    for index, chunk in enumerate(chunks):
        text = str(chunk.get("text") or "").strip()
        if not text:
            continue
        category = str(chunk.get("category") or "document").strip() or "document"
        source = str(chunk.get("source") or chunk.get("filename") or "").strip()
        overlap = len(query_tokens & _context_tokens(text))
        source_suffix = f", {source}" if source else ""
        ranked.append((overlap, -index, f"Chunk {index + 1} ({category}{source_suffix}):\n{text}"))

    if not ranked:
        return (
            "\nRequirement/document context from BRD/FSD/WBS/assumptions:\n"
            "  (no BRD/FSD/WBS/assumption chunks found; use HLS and app evidence only)"
        )

    ranked.sort(reverse=True)
    relevant = [fragment for score, _, fragment in ranked if score > 0][:6]
    if not relevant:
        relevant = [fragment for _, _, fragment in ranked[:4]]
    batches = build_text_batches(relevant, max_chars=4500, max_items=6)
    if not batches:
        return (
            "\nRequirement/document context from BRD/FSD/WBS/assumptions:\n"
            "  (no compact document context available; use HLS and app evidence only)"
        )
    return (
        "\nRequirement/document context from BRD/FSD/WBS/assumptions "
        "(QA evidence; keep tests executable):\n"
        f"{batches[0]}"
    )


# JSON escape characters that are valid after a backslash.
_VALID_JSON_ESCAPE = set('"\\/bfnrtu')


def _repair_invalid_escapes(s: str) -> str:
    """Double up backslashes that don't begin a valid JSON escape.

    LLMs frequently emit unescaped backslashes inside string values (regex
    fragments, Windows paths, escaped quotes in step descriptions). Strict
    `json.loads` rejects these. We rewrite any `\\X` where X is not in the
    JSON escape set to `\\\\X`, leaving valid escapes (`\\n`, `\\"`, …) alone.
    """
    out = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\\" and i + 1 < n and s[i + 1] not in _VALID_JSON_ESCAPE:
            out.append("\\\\")
        else:
            out.append(c)
        i += 1
    return "".join(out)


def _parse_plan(raw: str) -> list[dict[str, Any]]:
    """Parse the JSON array from an LLM response, tolerating common pitfalls.

    Pitfall 1: prose / markdown fence wrapping → handled by '[...]' slicing.
    Pitfall 2: invalid backslash escapes inside strings → one-shot repair pass.
    Pitfall 3: truncated output (free-tier model output ceiling) → strip the
    last incomplete object and return the complete items we have.
    """
    cleaned = raw.strip()
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start == -1 or end == -1:
        # Try partial recovery: no closing ']' means the output was cut off.
        # Walk back from the end to find the last complete '}'.
        if start != -1:
            last_close = cleaned.rfind("}", start)
            if last_close != -1:
                candidate = cleaned[start: last_close + 1] + "]"
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    pass
        raise ValueError("No JSON array found in LLM response")
    payload = cleaned[start: end + 1]
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        err = str(exc)
        if "Invalid \\escape" in err or "escape" in err.lower():
            try:
                return json.loads(_repair_invalid_escapes(payload))
            except json.JSONDecodeError:
                pass
        # Partial recovery: strip the last incomplete object (truncated output).
        last_close = payload.rfind("}", 0, len(payload) - 1)
        if last_close != -1:
            candidate = payload[: last_close + 1] + "]"
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass
        raise


def _make_tc_number(sequence: int) -> str:
    return f"TC-{sequence:03d}"


def _infer_auth_mode(title: str, steps: list[str]) -> str:
    """Classify a test case's auth requirement so A5 can wire storageState.

    Three modes (consumed by A5._requires_anonymous_start):
      - "login_flow"   — exercises the auth UI; MUST start without stored session
      - "anonymous"    — public page, no session required; MUST start without
      - "authenticated" — needs the run's stored session loaded

    Both signup and register CREATE accounts, which means they:
      a) need to start anonymous (no existing session), and
      b) submit credential forms — same handling as login.
    They are therefore classified `login_flow`, not `anonymous`. Previously
    "signup"/"sign up" landed in anonymous_terms which was inconsistent with
    "register" landing in login_terms.
    """
    title_text = (title or "").lower()
    text = " ".join([title, *steps]).lower()
    auth_behavior_terms = (
        "login",
        "log in",
        "sign in",
        "signin",
        "logout",
        "log out",
        "sign out",
        "signout",
        "register",
        "signup",
        "sign up",
        "forgot password",
        "reset password",
    )
    authenticated_setup_terms = (
        "authenticated user",
        "logged-in user",
        "logged in user",
        "already authenticated",
        "already logged in",
        "stored session",
        "credential profile",
        "project credential",
    )
    if any(term in title_text for term in authenticated_setup_terms) and not any(
        term in title_text for term in auth_behavior_terms
    ):
        return "authenticated"
    if (
        any(term in text for term in ("credential profile", "project credential"))
        and not any(term in title_text for term in auth_behavior_terms)
    ):
        return "authenticated"
    login_terms = (
        "login",
        "log in",
        "sign in",
        "signin",
        "logout",
        "log out",
        "sign out",
        "signout",
        "register",
        "signup",
        "sign up",
        "password",
        "forgot password",
        "reset password",
    )
    if any(term in text for term in login_terms):
        return "login_flow"
    anonymous_terms = ("public", "landing", "home page")
    if any(term in text for term in anonymous_terms):
        return "anonymous"
    return "authenticated"


_AUTH_MODES = {"authenticated", "login_flow", "anonymous"}


def _normalize_auth_mode(value: Any) -> str | None:
    raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "auth": "authenticated",
        "logged_in": "authenticated",
        "loggedin": "authenticated",
        "requires_auth": "authenticated",
        "requires_login": "authenticated",
        "login": "login_flow",
        "auth_flow": "login_flow",
        "authentication_flow": "login_flow",
        "unauthenticated": "anonymous",
        "public": "anonymous",
        "guest": "anonymous",
    }
    normalized = aliases.get(raw, raw)
    return normalized if normalized in _AUTH_MODES else None


def _auth_mode_for_item(item: dict[str, Any]) -> str:
    """Use model-emitted auth_mode first; infer only as fallback."""
    explicit = _normalize_auth_mode(item.get("auth_mode"))
    if explicit:
        return explicit
    return _infer_auth_mode(
        str(item.get("title") or ""),
        [str(step) for step in (item.get("steps") or [])],
    )


def _item_has_login_step(item: dict[str, Any]) -> bool:
    return _contains_any(
        " ".join(str(step) for step in (item.get("steps") or [])).lower(),
        (
            "login",
            "log in",
            "sign in",
            "signin",
            "credential profile",
            "project credential",
            "{test_username}",
            "{test_password}",
            "test_username",
            "test_password",
        ),
    )


def _ensure_inline_login_setup(item: dict[str, Any]) -> dict[str, Any]:
    """For inline-auth mode, authenticated business tests must include login setup."""
    auth_mode = _auth_mode_for_item(item)
    if auth_mode != "authenticated" or _item_has_login_step(item):
        updated = dict(item)
        updated["auth_mode"] = auth_mode
        return updated

    updated = dict(item)
    updated["auth_mode"] = auth_mode
    steps = [str(step) for step in (item.get("steps") or [])]
    updated["steps"] = [
        'login using the project credential profile for the required role',
        *steps,
    ]
    return updated


def _role_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", (value or "").lower())
        if len(token) > 1
    }


def _profile_roles_for_project(project_id: str) -> list[str]:
    try:
        with SessionLocal() as db:
            rows = db.execute(
                select(CredentialProfile.role)
                .where(CredentialProfile.project_id == uuid.UUID(project_id))
                .distinct()
            ).scalars().all()
    except Exception:
        return []
    return [str(role).strip() for role in rows if str(role or "").strip()]


def _infer_credential_role(
    *,
    project_id: str,
    title: str,
    steps: list[str],
    hls_title: str,
    hls_description: str,
    document_context: str,
) -> str | None:
    """Infer role intent only from uploaded credential roles and supplied evidence.

    We intentionally do not invent roles. If the project uploaded roles like
    `admin`, `manager`, or `locked_out_user`, A3 may attach one only when that
    role is mentioned in the testcase/HLS/document context.
    """
    roles = _profile_roles_for_project(project_id)
    if not roles:
        return None
    evidence = " ".join([title, *steps, hls_title, hls_description, document_context]).lower()
    evidence_tokens = _role_tokens(evidence)

    matches: list[tuple[int, str]] = []
    for role in roles:
        normalized = role.lower().strip()
        role_token_set = _role_tokens(role)
        role_phrase = normalized.replace("_", " ").replace("-", " ")
        score = 0
        if normalized and normalized in evidence:
            score += 4
        if role_phrase and role_phrase in evidence:
            score += 4
        if role_token_set and role_token_set <= evidence_tokens:
            score += 2 + len(role_token_set)
        if score:
            matches.append((score, role))
    if not matches:
        return None
    matches.sort(key=lambda item: (-item[0], item[1].lower()))
    if len(matches) > 1 and matches[0][0] == matches[1][0]:
        return None
    return matches[0][1]


_AUTH_TERMS = (
    "login",
    "log in",
    "sign in",
    "signin",
    "authenticate",
    "authentication",
    "password",
    "credential",
    "credentials",
    "logout",
    "log out",
    "sign out",
    "register",
    "signup",
    "sign up",
)

_AUTH_NEGATIVE_TERMS = (
    "invalid",
    "empty",
    "blank",
    "locked",
    "blocked",
    "expired",
    "forgot",
    "reset",
    "logout",
    "log out",
    "sign out",
    "failure",
    "error",
    "denied",
)

_BUSINESS_FLOW_TERMS = (
    "browse",
    "search",
    "filter",
    "sort",
    "booking",
    "reservation",
    "dashboard",
    "report",
    "invoice",
    "profile",
    "account",
    "claim",
    "policy",
    "application",
    "form",
    "upload",
    "download",
    "submit",
    "approve",
    "reject",
    "create",
    "edit",
    "delete",
    "update",
    "notification",
    "message",
    "record",
    "item",
    "request",
    "task",
    "workflow",
    "transaction",
)

_DESTRUCTIVE_BRIDGE_TERMS = (
    "cancel",
    "clear",
    "delete",
    "discard",
    "remove",
    "reset",
)

_LIFECYCLE_TERMS = (
    "create",
    "created",
    "new",
    "edit",
    "update",
    "delete",
    "remove",
    "same",
    "existing",
    "record",
    "customer",
    "user",
    "account",
    "profile",
    "case",
    "claim",
    "application",
)


def _text_from_item(item: dict[str, Any]) -> str:
    steps = item.get("steps") or []
    ac = item.get("acceptance_criteria") or []
    return " ".join(
        [
            str(item.get("title") or ""),
            *(str(s) for s in steps),
            *(str(c) for c in ac),
        ]
    ).lower()


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


_SPECIFIC_INVALID_EVIDENCE_TERMS = (
    "invalid",
    "format",
    "pattern",
    "must be",
    "must contain",
    "minimum",
    "maximum",
    "too short",
    "too long",
    "out of range",
    "not allowed",
    "duplicate",
    "already exists",
    "unsupported",
)


_VAGUE_INVALID_INPUT_TERMS = (
    "invalid login",
    "invalid credentials",
    "invalid email",
    "invalid password",
    "invalid value",
    "invalid input",
    "invalid data",
    "invalid workflow",
    "invalid submission",
    "invalid shipping",
    "invalid details",
    "invalid field",
    "invalid fields",
    "invalid first name",
    "invalid last name",
    "invalid postal",
    "with an invalid",
)


def _evidence_text(recorded_steps: list[dict[str, Any]] | None) -> str:
    parts: list[str] = []
    for step in recorded_steps or []:
        for key in ("action_type", "url", "selector", "value", "text"):
            value = step.get(key)
            if value:
                parts.append(str(value))
    return " ".join(parts).lower()


def _normalise_unsupported_invalid_input_case(
    item: dict[str, Any],
    evidence_text: str,
) -> dict[str, Any]:
    """Prefer required-field negatives over unsupported invalid-value guesses.

    A3 may reasonably create negative tests, but vague "invalid value" data is
    risky unless the source context shows that rule. Missing required-field
    validation is the safer generic form-validation negative because A5 can
    ground it in recorded form fields without inventing app-specific formats.
    """
    item_text = _text_from_item(item)
    if not _contains_any(item_text, _VAGUE_INVALID_INPUT_TERMS):
        return item
    if _contains_any(item_text, _AUTH_TERMS):
        has_documented_negative_auth = _contains_any(
            evidence_text,
            ("locked", "blocked", "disabled", "suspended", "inactive", "invalid credentials"),
        )
        if not has_documented_negative_auth:
            replacements = (
                ("fill username field with invalid email", "leave username field empty"),
                ("fill username with invalid email", "leave username field empty"),
                ("fill email field with invalid email", "leave email field empty"),
                ("fill email with invalid email", "leave email field empty"),
                ("fill password field with invalid password", "leave password field empty"),
                ("fill password with invalid password", "leave password field empty"),
                ("invalid login", "required login fields"),
                ("Invalid Login", "Required Login Fields"),
                ("invalid credentials", "missing required credentials"),
                ("Invalid credentials", "Missing required credentials"),
                ("invalid email", "missing username"),
                ("invalid password", "missing password"),
            )

            def replace_auth_text(value: Any) -> Any:
                if not isinstance(value, str):
                    return value
                text = value
                for old, new in replacements:
                    text = text.replace(old, new)
                return text

            item = dict(item)
            item["title"] = replace_auth_text(item.get("title"))
            item["steps"] = [replace_auth_text(step) for step in item.get("steps") or []]
            item["acceptance_criteria"] = [
                replace_auth_text(criteria)
                for criteria in item.get("acceptance_criteria") or []
            ]
        return item
    if _contains_any(evidence_text, _SPECIFIC_INVALID_EVIDENCE_TERMS):
        return item

    replacements = (
        ("fill first name with an invalid value", "leave first name empty"),
        ("fill last name with an invalid value", "leave last name empty"),
        ("fill postal code with an invalid value", "leave postal code empty"),
        ("fill postcode with an invalid value", "leave postcode empty"),
        ("fill zip code with an invalid value", "leave zip code empty"),
        ("enter invalid or missing data", "leave required data empty"),
        ("assert the invalid workflow is not completed", "assert the incomplete workflow is not completed"),
        ("No invalid record or state transition is created", "No incomplete record or state transition is created"),
        ("Invalid input is rejected", "Missing required input is rejected"),
        ("Invalid", "Missing Required"),
        ("invalid", "missing required"),
        ("with an missing required", "with a missing required"),
        ("missing required workflow", "incomplete workflow"),
        ("missing required submission", "incomplete submission"),
        ("missing required shipping", "missing required shipping fields"),
    )

    def replace_text(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        text = value
        for old, new in replacements:
            text = text.replace(old, new)
        return text

    item = dict(item)
    item["title"] = replace_text(item.get("title"))
    item["steps"] = [replace_text(step) for step in item.get("steps") or []]
    item["acceptance_criteria"] = [
        replace_text(criteria)
        for criteria in item.get("acceptance_criteria") or []
    ]
    return item


def _step_tokens(step: Any) -> set[str]:
    text = str(step).lower()
    for ch in ",.;:()[]{}#'\"":
        text = text.replace(ch, " ")
    stop = {
        "a", "an", "and", "the", "to", "with", "for", "of", "in", "on",
        "at", "is", "are", "be", "as", "valid", "visible", "displayed",
        "assert", "click", "fill", "navigate", "wait", "page",
    }
    return {tok for tok in text.split() if len(tok) > 2 and tok not in stop}


def _step_overlap_ratio(candidate_steps: list[Any], previous_steps: list[Any]) -> float:
    if not candidate_steps or not previous_steps:
        return 0.0
    prev_tokens = set().union(*(_step_tokens(step) for step in previous_steps))
    if not prev_tokens:
        return 0.0
    overlapping = 0
    for step in candidate_steps:
        tokens = _step_tokens(step)
        if tokens and len(tokens & prev_tokens) / max(len(tokens), 1) >= 0.6:
            overlapping += 1
    return overlapping / len(candidate_steps)


def _step_contains_selector(step: Any, selector: str) -> bool:
    if not selector:
        return False
    return selector.lower() in str(step).lower()


def _path_from_recorded_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return path


def _css_attr_value(value: Any) -> str:
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"')


def _selector_from_attrs(tag: str, attrs: dict[str, str]) -> str | None:
    lowered = {k.lower(): v for k, v in attrs.items() if v is not None and str(v).strip()}
    for attr in _STABLE_ATTR_PRIORITY:
        value = lowered.get(attr)
        if value:
            return f'[{attr}="{_css_attr_value(value)}"]'
    class_value = lowered.get("class", "").strip()
    if class_value and all(token and re.match(r"^[A-Za-z_][\w-]*$", token) for token in class_value.split()):
        return f"{tag.lower()}." + ".".join(class_value.split())
    return None


def _stable_selector_from_snapshot_text(snapshot: dict[str, Any], text: str) -> str | None:
    if not text:
        return None
    matches: list[str] = []
    for element in snapshot.get("interactive_elements") or []:
        if str(element.get("text") or "").strip() != text.strip():
            continue
        selector = str(element.get("selector") or "").strip()
        if selector and selector.lower() not in _BARE_TAG_SELECTORS:
            matches.append(selector)
    unique = sorted(set(matches))
    return unique[0] if len(unique) == 1 else None


def _stable_anchor_selector_containing_text(html: str, text: str) -> str | None:
    if not text:
        return None
    pattern = re.compile(
        r"<a\b(?P<attrs>[^>]*)>(?P<body>(?:(?!</a>).)*?\b" + re.escape(text.strip()) + r"\b(?:(?!</a>).)*?)</a>",
        re.IGNORECASE | re.DOTALL,
    )
    selectors: list[str] = []
    for match in pattern.finditer(html or ""):
        attrs: dict[str, str] = {}
        for attr_match in _HTML_ATTR_RE.finditer(match.group("attrs") or ""):
            name = (attr_match.group("name") or "").strip()
            if not name:
                continue
            value = attr_match.group("quoted")
            if value is None:
                value = attr_match.group("bare") or ""
            attrs[name] = value
        selector = _selector_from_attrs("a", attrs)
        if selector:
            selectors.append(selector)
    unique = sorted(set(selectors))
    return unique[0] if len(unique) == 1 else None


def _snapshot_for_recorded_step(project_id: str | None, recorded_step: dict[str, Any]) -> dict[str, Any] | None:
    if not project_id:
        return None
    url = str(recorded_step.get("url") or "").strip()
    if not url:
        return None
    path = _path_from_recorded_url(url)
    for candidate in (path, path.split("?", 1)[0]):
        try:
            return mcp_server.get_snapshot(project_id, candidate)
        except Exception:
            continue
    return None


def _stable_selector_for_recorded_step(
    recorded_step: dict[str, Any],
    project_id: str | None = None,
) -> str:
    selector = str(recorded_step.get("selector") or "").strip()
    if selector and selector.lower() not in _BARE_TAG_SELECTORS:
        return selector

    text = str(
        recorded_step.get("element_text")
        or recorded_step.get("text")
        or recorded_step.get("name")
        or ""
    ).strip()
    snapshot = _snapshot_for_recorded_step(project_id, recorded_step) or {}
    selector_from_text = _stable_selector_from_snapshot_text(snapshot, text)
    if selector_from_text:
        return selector_from_text
    if selector.lower() == "a" or str(recorded_step.get("element_type") or "").lower() == "a":
        selector_from_anchor = _stable_anchor_selector_containing_text(str(snapshot.get("html") or ""), text)
        if selector_from_anchor:
            return selector_from_anchor
    return selector


def _bridge_step_from_recorded(
    recorded_step: dict[str, Any],
    project_id: str | None = None,
) -> str:
    action = str(recorded_step.get("action_type") or recorded_step.get("action") or "").strip().lower()
    selector = _stable_selector_for_recorded_step(recorded_step, project_id)
    text = str(
        recorded_step.get("element_text")
        or recorded_step.get("text")
        or recorded_step.get("name")
        or ""
    ).strip()
    url = str(recorded_step.get("url") or "").strip()

    if action in {"navigate", "goto", "open"} and url:
        path = _path_from_recorded_url(url)
        return f"navigate to {{{{BASE_URL}}}}{path}"
    if selector and selector.lower() not in _BARE_TAG_SELECTORS:
        verb = "click" if not action or action == "unknown" else action
        return f"{verb} {selector}"
    # Avoid preserving ambiguous text-only links/buttons such as notification
    # counters ("1"). If there is no stable selector, a text-only bridge must
    # be descriptive enough to be reviewable and automatable.
    if text and len(text) >= 3 and re.search(r"[A-Za-z]", text):
        verb = "click" if not action or action == "unknown" else action
        return f"{verb} {text}"
    return ""


def _route_semantic_label(path: str) -> str | None:
    """Convert a recorded destination route into a reviewable UI target label.

    This avoids app-specific selectors while keeping UI tests user-like:
    `/records.html` -> `records`, `/case-review` -> `case review`.
    """
    clean = str(path or "").split("?", 1)[0].split("#", 1)[0].strip("/")
    if not clean:
        return None
    segment = clean.split("/")[-1]
    segment = re.sub(r"\.(html?|aspx?|php)$", "", segment, flags=re.IGNORECASE)
    segment = segment.replace("-", " ").replace("_", " ").strip()
    if not segment or segment.isdigit():
        return None
    return segment


def _semantic_bridge_step_from_route(path: str) -> str | None:
    label = _route_semantic_label(path)
    if not label:
        return None
    return f"click the {label} link"


def _is_bridge_recorded_action(recorded_step: dict[str, Any]) -> bool:
    action = str(recorded_step.get("action_type") or recorded_step.get("action") or "").strip().lower()
    if action in {"click", "navigate", "goto", "open"}:
        return True
    if action in {"", "unknown"} and (recorded_step.get("selector") or recorded_step.get("url")):
        return True
    return False


def _recorded_step_has_destructive_bridge_action(recorded_step: dict[str, Any]) -> bool:
    text = " ".join(
        str(recorded_step.get(key) or "")
        for key in ("selector", "element_text", "text", "name", "aria_label", "value")
    ).lower()
    return _contains_any(text, _DESTRUCTIVE_BRIDGE_TERMS)


def _recorded_step_is_transition_bridge(recorded_step: dict[str, Any]) -> bool:
    """Return True for recorded actions likely to expose a later control.

    This stays app-neutral: it looks for stable navigation or controls whose
    selector/text indicates moving to another workflow surface. It does not
    hardcode any application route or selector.
    """
    action = str(recorded_step.get("action_type") or recorded_step.get("action") or "").strip().lower()
    if action in {"navigate", "goto", "open"}:
        return True
    text = " ".join(
        str(recorded_step.get(key) or "")
        for key in ("selector", "element_text", "text", "name", "aria_label")
    ).lower()
    return _contains_any(
        text,
        (
            "continue",
            "next",
            "review",
            "summary",
            "details",
            "confirm",
            "submit",
            "save",
            "update",
            "remove",
        ),
    )


def _preserve_recorded_bridge_steps(
    item: dict[str, Any],
    recorded_steps: list[dict[str, Any]] | None,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Insert skipped recorded bridge actions between two planned recorded controls.

    This is intentionally app-neutral. It only uses ordering from Phase 2
    evidence: if A3 uses selector A and later selector C, but the recording
    shows a click/navigation B between them, B may be required to expose C.
    """
    if not recorded_steps:
        return item
    planned_steps = [str(step) for step in item.get("steps") or []]
    if len(planned_steps) < 2:
        return item

    matched: list[tuple[int, int]] = []
    for plan_index, step in enumerate(planned_steps):
        for recorded_index, recorded_step in enumerate(recorded_steps):
            selector = str(recorded_step.get("selector") or "").strip()
            if selector and _step_contains_selector(step, selector):
                matched.append((plan_index, recorded_index))
                break

    if len(matched) < 2:
        return item

    insertions_by_plan_index: dict[int, list[str]] = {}
    planned_text = "\n".join(planned_steps).lower()
    for (left_plan_index, left_recorded_index), (right_plan_index, right_recorded_index) in zip(matched, matched[1:]):
        left_path = _path_from_recorded_url(str(recorded_steps[left_recorded_index].get("url") or ""))
        right_path = _path_from_recorded_url(str(recorded_steps[right_recorded_index].get("url") or ""))
        if right_recorded_index <= left_recorded_index + 1:
            continue
        existing_text = planned_text
        bridge_steps: list[str] = []
        saw_unusable_bridge_candidate = False
        for recorded_step in recorded_steps[left_recorded_index + 1:right_recorded_index]:
            if not _is_bridge_recorded_action(recorded_step):
                continue
            if (
                _recorded_step_has_destructive_bridge_action(recorded_step)
                and not _contains_any(planned_text, _DESTRUCTIVE_BRIDGE_TERMS)
            ):
                continue
            bridge_step = _bridge_step_from_recorded(recorded_step, project_id)
            if not bridge_step:
                saw_unusable_bridge_candidate = True
                continue
            if not _recorded_step_is_transition_bridge(recorded_step):
                saw_unusable_bridge_candidate = True
                continue
            if bridge_step.lower() not in existing_text:
                bridge_steps.append(bridge_step)
                existing_text += f"\n{bridge_step.lower()}"
        if (
            not bridge_steps
            and saw_unusable_bridge_candidate
            and right_path
            and left_path
            and right_path != left_path
            and right_path.lower() not in existing_text
        ):
            bridge_steps.append(
                _semantic_bridge_step_from_route(right_path)
                or f"navigate to {{{{BASE_URL}}}}{right_path}"
            )
        if bridge_steps:
            insertions_by_plan_index.setdefault(right_plan_index, []).extend(bridge_steps)

    if not insertions_by_plan_index:
        return item

    new_steps: list[str] = []
    for index, step in enumerate(planned_steps):
        new_steps.extend(insertions_by_plan_index.get(index, []))
        new_steps.append(step)

    updated = dict(item)
    updated["steps"] = new_steps
    logger.info(
        "agent3: inserted %d recorded bridge steps for testcase %r",
        len(new_steps) - len(planned_steps),
        item.get("title"),
    )
    return updated


def _step_has_unresolved_bare_selector(step: str) -> bool:
    parsed = _step_action_selector(step)
    return bool(parsed and parsed[1].lower() in _BARE_TAG_SELECTORS)


def _step_has_destructive_action(step: str) -> bool:
    parsed = _step_action_selector(step)
    if not parsed:
        return False
    action, selector = parsed
    text = f"{action} {selector} {step}".lower()
    return _contains_any(text, _DESTRUCTIVE_BRIDGE_TERMS)


def _testcase_allows_destructive_steps(item: dict[str, Any]) -> bool:
    # Destructive actions are valid only when the test case itself is about
    # that behavior, not when an LLM/replay accidentally inserts them in a
    # happy-path validation flow.
    intent_text = " ".join(
        [
            str(item.get("title") or ""),
            *(str(criteria) for criteria in (item.get("acceptance_criteria") or [])),
        ]
    ).lower()
    return _contains_any(intent_text, _DESTRUCTIVE_BRIDGE_TERMS)


def _step_action_selector(step: Any) -> tuple[str, str] | None:
    text = str(step or "").strip()
    match = re.match(r"^(click|submit|tap|press|fill|select|check|uncheck)\s+([^\s]+)", text, re.IGNORECASE)
    if not match:
        return None
    action = match.group(1).lower()
    if action in {"submit", "tap", "press"}:
        action = "click"
    return action, match.group(2).strip()


def _step_is_redundant_focus_click(step: Any, all_steps: list[str]) -> bool:
    parsed = _step_action_selector(step)
    if not parsed:
        return False
    action, selector = parsed
    if action != "click" or not selector:
        return False
    lower_selector = selector.lower()
    if not (
        lower_selector.startswith("#")
        or lower_selector.startswith("input")
        or any(hint in lower_selector for hint in _FIELD_SELECTOR_HINTS)
    ):
        return False
    fill_prefix = f"fill {selector.lower()} "
    return any(str(other).strip().lower().startswith(fill_prefix) for other in all_steps)


def _is_required_field_validation_case(item: dict[str, Any]) -> bool:
    text = _text_from_item(item)
    return _contains_any(
        text,
        (
            "required field",
            "required fields",
            "missing required",
            "empty field",
            "empty fields",
            "without filling",
            "leave ",
            " left empty",
        ),
    )


def _leave_empty_field_name(step: str) -> str | None:
    text = str(step or "").strip()
    match = re.match(
        r"^leave\s+(.+?)\s+(?:empty|blank|unfilled)$",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    return match.group(1).strip()


def _field_tokens(field: str) -> set[str]:
    text = re.sub(r"\{[^}]*\}", " ", str(field or "").lower())
    text = text.replace("#", " ").replace("-", " ").replace("_", " ")
    for ch in ",.;:()[]{}'\"":
        text = text.replace(ch, " ")
    stop = {"field", "input", "textbox", "box", "the", "a", "an"}
    tokens = {tok for tok in text.split() if len(tok) > 1 and tok not in stop}
    expansions = {
        "username": {"user", "name"},
        "userid": {"user", "id"},
        "email": {"user", "email"},
        "postcode": {"postal", "code"},
        "zipcode": {"zip", "code"},
        "firstname": {"first", "name"},
        "lastname": {"last", "name"},
    }
    expanded = set(tokens)
    for token in tokens:
        expanded.update(expansions.get(token, set()))
    return expanded


def _valid_value_for_field(field: str, recorded_value: str | None = None) -> str:
    tokens = _field_tokens(field)
    if {"password", "pass"} & tokens:
        return "{TEST_PASSWORD}"
    if {"user", "username", "email", "login"} & tokens:
        return "{TEST_USERNAME}"
    if {"postal", "postcode", "zip", "zipcode", "pin", "pincode"} & tokens:
        return recorded_value or "a valid postal code"
    if {"first", "firstname", "given"} & tokens:
        return recorded_value or "a valid first name"
    if {"last", "lastname", "surname", "family"} & tokens:
        return recorded_value or "a valid last name"
    if recorded_value:
        return recorded_value
    return "a valid value"


def _matching_recorded_fill(
    field: str,
    recorded_steps: list[dict[str, Any]] | None,
    project_id: str | None,
) -> tuple[str | None, str | None]:
    field_tokens = _field_tokens(field)
    if not field_tokens:
        return None, None
    for recorded_step in recorded_steps or []:
        action = str(recorded_step.get("action_type") or recorded_step.get("action") or "").strip().lower()
        if action != "fill":
            continue
        selector = _stable_selector_for_recorded_step(recorded_step, project_id)
        if not selector:
            continue
        recorded_text = _recorded_step_text(recorded_step)
        recorded_tokens = _field_tokens(f"{selector} {recorded_text}")
        if not (field_tokens & recorded_tokens):
            continue
        value = str(recorded_step.get("value") or "").strip() or None
        return selector, value
    return None, None


def _valid_fill_step_for_field(
    field: str,
    recorded_steps: list[dict[str, Any]] | None,
    project_id: str | None,
) -> str:
    selector, recorded_value = _matching_recorded_fill(field, recorded_steps, project_id)
    target = selector or field
    return f"fill {target} with {_valid_value_for_field(field, recorded_value)}"


def _assertion_for_missing_field(original_assertions: list[str], field: str) -> str:
    field_text = str(field or "").strip()
    field_tokens = _field_tokens(field_text)
    for assertion in original_assertions:
        assertion_tokens = _field_tokens(assertion)
        if field_tokens and field_tokens & assertion_tokens:
            return assertion
    label = field_text
    if label.startswith("#"):
        label = label[1:].replace("-", " ").replace("_", " ")
    return f"assert validation feedback is visible for missing {label}"


def _is_submit_or_transition_click(step: str) -> bool:
    parsed = _step_action_selector(step)
    lower = str(step).lower()
    if not parsed and lower.strip().startswith(("submit", "continue", "finish", "save", "next", "create")):
        return True
    if not parsed:
        return False
    action, selector = parsed
    if action != "click":
        return False
    if _contains_any(lower, ("submit", "login", "continue", "save", "create", "finish", "next")):
        return True
    selector_lower = selector.lower()
    if selector_lower in _BARE_TAG_SELECTORS:
        return False
    return not any(hint in selector_lower for hint in _FIELD_SELECTOR_HINTS)


def _structure_required_field_validation_steps(
    item: dict[str, Any],
    steps: list[str],
    recorded_steps: list[dict[str, Any]] | None,
    project_id: str | None,
) -> list[str]:
    """Split combined required-field checks into one submit per missing field.

    Real apps often report one blocking required-field error at a time. A test
    that leaves every field empty and expects every message from one submit is
    brittle, so we turn it into independent validation checks while preserving
    the same testcase.
    """
    if not _is_required_field_validation_case(item):
        return steps

    leave_positions: list[tuple[int, str]] = []
    for index, step in enumerate(steps):
        field = _leave_empty_field_name(step)
        if field:
            leave_positions.append((index, field))
    unique_fields = []
    for _index, field in leave_positions:
        if field not in unique_fields:
            unique_fields.append(field)
    if len(unique_fields) < 2:
        return steps

    first_leave = leave_positions[0][0]
    last_leave = leave_positions[-1][0]
    setup_steps = [
        step for step in steps[:first_leave]
        if not str(step).strip().lower().startswith(("assert", "verify", "validate"))
    ]

    submit_step = next(
        (step for step in steps[last_leave + 1:] if _is_submit_or_transition_click(step)),
        None,
    )
    if not submit_step:
        return steps

    original_assertions = [
        step for step in steps[last_leave + 1:]
        if str(step).strip().lower().startswith(("assert", "verify", "validate"))
    ]

    structured: list[str] = []
    for field in unique_fields:
        if structured:
            for nav in setup_steps:
                if str(nav).strip().lower().startswith("navigate"):
                    structured.append(nav)
                    break
        else:
            structured.extend(setup_steps)

        for other_field in unique_fields:
            if other_field == field:
                continue
            fill_step = _valid_fill_step_for_field(other_field, recorded_steps, project_id)
            if fill_step not in structured[-len(unique_fields):]:
                structured.append(fill_step)
        structured.append(f"leave {field} empty")
        structured.append(submit_step)
        structured.append(_assertion_for_missing_field(original_assertions, field))

    return structured


def _step_is_validation_focus_noise(step: Any, item: dict[str, Any]) -> bool:
    if not _is_required_field_validation_case(item):
        return False
    parsed = _step_action_selector(step)
    if not parsed:
        return False
    action, selector = parsed
    if action != "click":
        return False
    lower_selector = selector.lower()
    if lower_selector in _BARE_TAG_SELECTORS:
        return True
    if lower_selector.startswith("#") or lower_selector.startswith("input"):
        return any(hint in lower_selector for hint in _FIELD_SELECTOR_HINTS)
    return any(hint in lower_selector for hint in _FIELD_SELECTOR_HINTS)


def _replace_bare_step_selector(
    step: str,
    recorded_steps: list[dict[str, Any]] | None,
    used_recorded: set[int],
    project_id: str | None,
) -> str:
    parsed = _step_action_selector(step)
    if not parsed or not recorded_steps:
        return step
    action, selector = parsed
    if selector.lower() not in _BARE_TAG_SELECTORS:
        return step

    for index, recorded_step in enumerate(recorded_steps):
        if index in used_recorded:
            continue
        recorded_action = str(recorded_step.get("action_type") or recorded_step.get("action") or "").strip().lower()
        recorded_selector = str(recorded_step.get("selector") or "").strip().lower()
        if recorded_action != action or recorded_selector != selector.lower():
            continue
        stable_selector = _stable_selector_for_recorded_step(recorded_step, project_id)
        if stable_selector and stable_selector.lower() not in _BARE_TAG_SELECTORS:
            used_recorded.add(index)
            return re.sub(
                r"^(\w+)\s+\S+",
                lambda match: f"{match.group(1)} {stable_selector}",
                step,
                count=1,
                flags=re.IGNORECASE,
            )
        break
    return step


def _step_has_explicit_selector(step: str) -> bool:
    return bool(re.search(r"(#[A-Za-z0-9_-]+|\.[A-Za-z][A-Za-z0-9_-]+|\[[^\]]+\]|[A-Za-z]+\[[^\]]+\])", step))


def _recorded_step_text(recorded_step: dict[str, Any]) -> str:
    return " ".join(
        str(recorded_step.get(key) or "")
        for key in ("selector", "element_text", "text", "name", "aria_label", "placeholder", "label", "value")
    )


def _resolve_prose_step_selector(
    step: str,
    recorded_steps: list[dict[str, Any]] | None,
    used_recorded: set[int],
    project_id: str | None,
) -> str:
    parsed = _step_action_selector(step)
    if not parsed or not recorded_steps or _step_has_explicit_selector(step):
        return step
    action, _first_token = parsed
    # Strip template variables like {{USER_EMAIL}} before token computation —
    # they are interpolation markers, not semantic descriptors, and would
    # dilute the overlap score (e.g. "user_email" counting against "username").
    step_for_tokens = re.sub(r"\{[^}]*\}", "", step)
    step_tokens = _step_tokens(step_for_tokens)
    if not step_tokens:
        return step

    matches: list[tuple[float, int, str]] = []
    for index, recorded_step in enumerate(recorded_steps):
        if index in used_recorded:
            continue
        recorded_action = str(recorded_step.get("action_type") or recorded_step.get("action") or "").strip().lower()
        if recorded_action != action:
            continue
        selector = _stable_selector_for_recorded_step(recorded_step, project_id)
        if not selector or selector.lower() in _BARE_TAG_SELECTORS:
            continue
        recorded_tokens = _step_tokens(_recorded_step_text(recorded_step))
        if not recorded_tokens:
            continue
        overlap = step_tokens & recorded_tokens
        if not overlap:
            continue
        score = len(overlap) / max(len(step_tokens), 1)
        # Require a meaningful match. This resolves "username field" ->
        # "#user-name" but avoids binding vague "click the button" to the
        # first recorded click.
        if score >= 0.34 or len(overlap) >= 2:
            matches.append((score, index, selector))

    matches.sort(key=lambda item: (-item[0], item[1]))
    if not matches:
        return step
    if len(matches) > 1 and matches[0][0] == matches[1][0]:
        return step

    _score, index, selector = matches[0]
    used_recorded.add(index)
    if action == "fill":
        value_match = re.search(r"\bwith\s+(.+)$", step, re.IGNORECASE)
        if value_match:
            return f"fill {selector} with {value_match.group(1).strip()}"
    if action == "select":
        option_match = re.search(r"\b(?:with|as|to|option)\s+(.+)$", step, re.IGNORECASE)
        if option_match:
            return f"select {option_match.group(1).strip()} from {selector}"
    return f"{action} {selector}"


def _navigate_path_from_step(step: str) -> str | None:
    text = str(step or "").strip()
    if not text.lower().startswith("navigate to "):
        return None
    target = re.sub(r"^navigate\s+to\s+", "", text, flags=re.IGNORECASE).strip()
    target = re.sub(r"^\{+\s*BASE_URL\s*\}+", "", target, flags=re.IGNORECASE).strip()
    if not target:
        return "/"
    if target.startswith("http://") or target.startswith("https://"):
        return _path_from_recorded_url(target)
    if not target.startswith("/"):
        target = f"/{target}"
    return target.split("#", 1)[0]


def _recorded_index_for_step(
    step: str,
    recorded_steps: list[dict[str, Any]] | None,
) -> int | None:
    for recorded_index, recorded_step in enumerate(recorded_steps or []):
        selector = str(recorded_step.get("selector") or "").strip()
        if selector and _step_contains_selector(step, selector):
            return recorded_index
    return None


def _recorded_route_reached_after(
    recorded_steps: list[dict[str, Any]] | None,
    recorded_index: int,
    path: str,
) -> bool:
    for recorded_step in (recorded_steps or [])[recorded_index + 1:]:
        recorded_path = _path_from_recorded_url(str(recorded_step.get("url") or ""))
        if recorded_path == path:
            return True
    return False


def _remove_redundant_midflow_navigations(
    steps: list[str],
    recorded_steps: list[dict[str, Any]] | None,
) -> list[str]:
    """Prefer recorded UI navigation over route teleporting inside a flow.

    We keep the first navigation as setup and keep navigations after assertions
    because those commonly reset independent validation checks. A later
    navigate is removed only when recorded evidence shows a previous UI action
    reaches that route.
    """
    if not recorded_steps:
        return steps

    cleaned: list[str] = []
    for step in steps:
        path = _navigate_path_from_step(step)
        if path is None or not cleaned:
            cleaned.append(step)
            continue

        previous_step = cleaned[-1]
        if str(previous_step).strip().lower().startswith(("assert", "verify", "validate")):
            cleaned.append(step)
            continue

        previous_recorded_index = _recorded_index_for_step(previous_step, recorded_steps)
        if (
            previous_recorded_index is not None
            and _recorded_route_reached_after(recorded_steps, previous_recorded_index, path)
        ):
            logger.info(
                "agent3: removed redundant mid-flow navigation to %s after %r",
                path,
                previous_step,
            )
            continue

        cleaned.append(step)
    return cleaned


def _clean_planned_steps(
    item: dict[str, Any],
    recorded_steps: list[dict[str, Any]] | None,
    project_id: str | None = None,
) -> dict[str, Any]:
    raw_steps = [str(step).strip() for step in item.get("steps") or [] if str(step).strip()]
    if not raw_steps:
        return item

    used_recorded: set[int] = set()
    cleaned: list[str] = []
    allow_destructive_steps = _testcase_allows_destructive_steps(item)
    for step in raw_steps:
        if _step_is_redundant_focus_click(step, raw_steps):
            continue
        if _step_is_validation_focus_noise(step, item):
            continue
        step = _replace_bare_step_selector(step, recorded_steps, used_recorded, project_id)
        step = _resolve_prose_step_selector(step, recorded_steps, used_recorded, project_id)
        if _step_has_unresolved_bare_selector(step):
            continue
        if _step_has_destructive_action(step) and not allow_destructive_steps:
            continue
        if step not in cleaned:
            cleaned.append(step)

    updated = dict(item)
    cleaned = _structure_required_field_validation_steps(
        updated,
        cleaned,
        recorded_steps,
        project_id,
    )
    cleaned = _remove_redundant_midflow_navigations(cleaned, recorded_steps)
    updated["steps"] = cleaned
    if len(cleaned) != len(raw_steps):
        logger.info(
            "agent3: cleaned %d noisy/replayed steps for testcase %r",
            len(raw_steps) - len(cleaned),
            item.get("title"),
        )
    return updated


def _has_assertion_step(item: dict[str, Any]) -> bool:
    return any(str(step).strip().lower().startswith(("assert", "verify", "validate")) for step in item.get("steps") or [])


def _ensure_business_assertion(item: dict[str, Any]) -> dict[str, Any]:
    if _has_assertion_step(item):
        return item
    updated = dict(item)
    steps = [str(step) for step in item.get("steps") or []]
    assertion = "assert the documented workflow result or state change is visible"
    steps.append(assertion)
    updated["steps"] = steps
    return updated


def _is_duplicate_dependent_tail_case(
    item: dict[str, Any],
    previous_items: list[dict[str, Any]],
) -> bool:
    """Detect cases that repeat another flow and only add a final assertion.

    A common bad output is:
      TC-002 Complete workflow
      TC-003 Validate final result (depends on TC-002)
    where TC-003 repeats almost every workflow step. In real suites that final
    result assertion belongs inside TC-002, not in a dependent duplicate.
    """
    raw_deps = item.get("depends_on") or []
    if not raw_deps:
        return False

    candidate_steps = item.get("steps") or []
    if len(candidate_steps) < 4:
        return False

    dep_titles = {str(dep).strip().lower() for dep in raw_deps}
    for prev in previous_items:
        if str(prev.get("title") or "").strip().lower() not in dep_titles:
            continue
        previous_steps = prev.get("steps") or []
        if not previous_steps:
            continue
        if _step_overlap_ratio(candidate_steps, previous_steps) >= 0.75:
            return True
    return False


def _is_true_lifecycle_dependency(
    item: dict[str, Any],
    previous_items: list[dict[str, Any]],
) -> bool:
    """Allow rare data lifecycle dependencies, clear normal UI-flow deps.

    We only keep dependencies when the dependent item and referenced item both
    read like a same-record lifecycle flow (create/update/delete same entity).
    This preserves architecture support without letting A3 chain normal UI
    flows by default.
    """
    raw_deps = item.get("depends_on") or []
    if not raw_deps:
        return False

    item_text = _text_from_item(item)
    if not _contains_any(item_text, _LIFECYCLE_TERMS):
        return False

    dep_titles = {str(dep).strip().lower() for dep in raw_deps}
    for prev in previous_items:
        prev_title = str(prev.get("title") or "").strip().lower()
        if prev_title not in dep_titles:
            continue
        combined = f"{_text_from_item(prev)} {item_text}"
        has_create = _contains_any(combined, ("create", "created", "new"))
        has_later_action = _contains_any(combined, ("edit", "update", "delete", "remove"))
        same_record_hint = _contains_any(combined, ("same", "existing", "record"))
        if has_create and has_later_action and same_record_hint:
            return True
    return False


def _is_prerequisite_only_auth_case(
    item: dict[str, Any],
    hls_title: str,
    hls_description: str,
) -> bool:
    """Return True when a broad HLS got reduced to a login-only test case.

    This is intentionally conservative: auth-negative cases still pass because
    they are real QA validations. We only reject positive login/setup cases when
    the HLS clearly includes a non-auth business flow and the item does not
    cover that flow at all.
    """
    hls_text = f"{hls_title} {hls_description}".lower()
    item_text = _text_from_item(item)

    hls_has_auth = _contains_any(hls_text, _AUTH_TERMS)
    hls_has_business_flow = _contains_any(hls_text, _BUSINESS_FLOW_TERMS)
    if not (hls_has_auth and hls_has_business_flow):
        return False

    item_has_auth = _contains_any(item_text, _AUTH_TERMS)
    item_has_business_flow = _contains_any(item_text, _BUSINESS_FLOW_TERMS)
    item_is_auth_negative = _contains_any(item_text, _AUTH_NEGATIVE_TERMS)

    return item_has_auth and not item_has_business_flow and not item_is_auth_negative


def _valid_planned_items(
    items: list[dict[str, Any]],
    pages: list[str],
    hls_title: str,
    hls_description: str,
    recorded_steps: list[dict[str, Any]] | None = None,
    document_context: str = "",
    project_id: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Apply deterministic A3 output guardrails before persistence.

    Returns (valid_items, granularity_reject_count). The count lets `plan()` ask
    the LLM once more when every candidate was filtered because it was too tiny
    for the broader HLS.
    """
    valid: list[dict[str, Any]] = []
    granularity_rejects = 0
    source_evidence_text = (
        f"{hls_title} {hls_description} {_evidence_text(recorded_steps)} {document_context}"
    ).lower()
    explicit_requirement_text = f"{hls_title} {hls_description} {document_context}".lower()

    for item in items:
        item = _normalise_unsupported_invalid_input_case(item, source_evidence_text)
        item = _ensure_inline_login_setup(item)
        # Resolve prose/bare planned actions before bridge insertion. Otherwise
        # a step like "click the Checkout button" cannot be matched to the
        # recorded transition action, and A3 misses required intermediate UI
        # surfaces such as review/detail pages.
        item = _clean_planned_steps(item, recorded_steps, project_id)
        item = _preserve_recorded_bridge_steps(item, recorded_steps, project_id)
        item = _clean_planned_steps(item, recorded_steps, project_id)
        item = _ensure_business_assertion(item)
        target_page = item.get("target_page", "")
        if pages and target_page not in pages:
            logger.warning(
                "agent3: target_page '%s' not in pages list — skipping", target_page
            )
            continue
        if not item.get("title") or not item.get("steps"):
            continue
        if _is_prerequisite_only_auth_case(item, hls_title, hls_description):
            granularity_rejects += 1
            logger.warning(
                "agent3: skipping prerequisite-only auth case %r for broader HLS %r",
                item.get("title"),
                hls_title,
            )
            continue
        if _is_duplicate_dependent_tail_case(item, valid):
            logger.warning(
                "agent3: skipping duplicate dependent tail case %r; merge its assertions into the parent flow",
                item.get("title"),
            )
            continue
        if item.get("depends_on") and not _is_true_lifecycle_dependency(item, valid):
            logger.warning(
                "agent3: clearing non-lifecycle depends_on for independent test case %r",
                item.get("title"),
            )
            item["depends_on"] = []
        leakage_warnings = _recording_leakage_warnings(
            item,
            recorded_steps,
            explicit_requirement_text,
        )
        if leakage_warnings:
            logger.warning(
                "agent3: rejecting Phase-2 replay leakage in testcase %r: %s",
                item.get("title"),
                leakage_warnings,
            )
            granularity_rejects += 1
            continue
        # Guarantee acceptance_criteria is always present
        if not item.get("acceptance_criteria"):
            item["acceptance_criteria"] = ["Test completes without errors"]
        valid.append(item)

    return valid, granularity_rejects


def _xray_hls_context(hls_items: list[tuple[str, str]]) -> str:
    if not hls_items:
        return "(no completed HLS available)"
    lines: list[str] = []
    for index, (title, description) in enumerate(hls_items[:40], 1):
        compact_description = " ".join(str(description or "").split())
        lines.append(f"{index}. {title} - {compact_description[:500]}")
    return "\n".join(lines)


def _xray_chunk_text_batches(project_id: str) -> tuple[list[str], int, list[dict[str, Any]]]:
    chunks = scroll_chunks(project_id, _BRD_CATEGORIES)
    fragments: list[str] = []
    usable_chunks: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunks, 1):
        text = str(chunk.get("text") or "").strip()
        if not text:
            continue
        usable_chunks.append(chunk)
        category = str(chunk.get("category") or "document").strip() or "document"
        fragments.append(f"Chunk {index} ({category}):\n{text}")
    return build_text_batches(fragments, max_chars=7000, max_items=3), len(fragments), usable_chunks


def _xray_test_case_context(tc_rows: list[dict[str, Any]]) -> str:
    if not tc_rows:
        return "(no automation test cases available)"
    lines: list[str] = []
    for index, tc in enumerate(tc_rows, 1):
        steps = tc.get("steps") or []
        ac = tc.get("acceptance_criteria") or []
        compact_steps = "; ".join(str(step) for step in steps[:8])
        compact_ac = "; ".join(str(item) for item in ac[:4])
        lines.append(
            f"{index}. {tc.get('title', '')}\n"
            f"   Steps: {compact_steps}\n"
            f"   Expected: {compact_ac}"
        )
    return "\n".join(lines)


def _normalise_xray_metadata_item(item: dict[str, Any]) -> dict[str, str]:
    def clean(value: Any, default: str = "") -> str:
        if isinstance(value, list):
            value = ",".join(str(v).strip() for v in value if str(v).strip())
        text = str(value or "").strip()
        return text or default

    precondition = clean(item.get("pre_condition_data") or item.get("preconditions"))
    sensitive_terms = ("password", "secret", "token", "api key")
    if any(term in precondition.lower() for term in sensitive_terms):
        precondition = "Valid user credentials are available"

    return {
        "labels": clean(item.get("labels"), "Functional"),
        "requirement": clean(item.get("requirement"), "TBD"),
        "priority": clean(item.get("priority"), "High"),
        "pre_condition_data": precondition or "Approved Phase 3 automation testcase",
    }


_XRAY_REQUIREMENT_KEY_RE = re.compile(
    r"\b(?P<key>(?:REQ|FR|NFR|BRD|PRD|FSD|US|STORY|UC|AC|SR)[-_ ]?\d{1,5}(?:\.\d+)?)\b",
    re.IGNORECASE,
)
_XRAY_TOKEN_STOP_WORDS = {
    "and", "are", "for", "from", "into", "page", "shall", "should", "test",
    "that", "the", "this", "user", "with", "without", "when", "then",
    "valid", "verify", "visible", "current", "application", "system",
}
_XRAY_REQUIREMENT_OVERRIDE_SCORE = 8
_XRAY_PRIMARY_INTENT_GROUPS = (
    {"add", "adding", "added", "create", "creating", "created"},
    {"remove", "removing", "removed", "delete", "deleting", "deleted", "clear", "clearing", "cleared"},
    {"required", "missing", "empty", "validation", "error", "prevents", "prevent", "continuation", "continue", "continuing"},
    {"detail", "details"},
    {"back", "return", "returns", "returned"},
    {"sort", "sorting", "order", "ascending", "descending"},
    {"logout", "logged", "session"},
    {"checkout", "confirmation", "complete", "completion", "confirms", "summary", "finish", "order"},
)
_XRAY_OPPOSING_INTENT_GROUPS = (
    (
        {"add", "adding", "added", "create", "creating", "created"},
        {"remove", "removing", "removed", "delete", "deleting", "deleted", "clear", "clearing", "cleared"},
    ),
)


def _normalise_requirement_key(value: Any) -> str:
    raw = str(value or "").strip().upper().replace("_", "-").replace(" ", "-")
    return re.sub(r"-+", "-", raw)


def _xray_tokens(value: Any) -> set[str]:
    tokens = set()
    for token in re.findall(r"[a-z0-9]+", str(value or "").lower()):
        if len(token) <= 2 or token in _XRAY_TOKEN_STOP_WORDS:
            continue
        tokens.add(token)
    return tokens


def _weighted_xray_tokens(*parts: tuple[Any, int]) -> dict[str, int]:
    weights: dict[str, int] = {}
    for value, weight in parts:
        for token in _xray_tokens(value):
            weights[token] = max(weights.get(token, 0), weight)
    return weights


def _xray_bigrams(value: Any) -> set[str]:
    tokens = [
        token for token in re.findall(r"[a-z0-9]+", str(value or "").lower())
        if len(token) > 2 and token not in _XRAY_TOKEN_STOP_WORDS
    ]
    return {" ".join(pair) for pair in zip(tokens, tokens[1:])}


def _score_requirement_segment(
    segment: dict[str, str],
    *,
    case_text: str,
    weighted_case_tokens: dict[str, int],
    title_text: str,
    single_requirement: bool,
) -> int:
    segment_text = segment["text"]
    segment_tokens = _xray_tokens(segment_text)
    title_tokens = _xray_tokens(title_text)
    score = sum(weight for token, weight in weighted_case_tokens.items() if token in segment_tokens)
    title_bigrams = _xray_bigrams(title_text)
    segment_bigrams = _xray_bigrams(segment_text)
    score += 4 * len(title_bigrams & segment_bigrams)
    for group in _XRAY_PRIMARY_INTENT_GROUPS:
        if not (title_tokens & group):
            continue
        if segment_tokens & group:
            score += 12
        else:
            score -= 18
    for left, right in _XRAY_OPPOSING_INTENT_GROUPS:
        if (title_tokens & left) and not (title_tokens & right) and (segment_tokens & right) and not (segment_tokens & left):
            score -= 20
        if (title_tokens & right) and not (title_tokens & left) and (segment_tokens & left):
            score -= 20
    if segment["requirement"].lower() in case_text.lower():
        score += 100
    if single_requirement:
        score += 1
    return score


def _requirement_segments_from_chunks(chunks: list[dict[str, Any]]) -> list[dict[str, str]]:
    segments: list[dict[str, str]] = []
    for chunk in chunks:
        text = str(chunk.get("text") or "").strip()
        if not text:
            continue
        lines = text.splitlines() or [text]
        requirement_line_indexes = [
            (index, match)
            for index, line in enumerate(lines)
            if (match := _XRAY_REQUIREMENT_KEY_RE.search(line))
        ]
        for item_index, (line_index, match) in enumerate(requirement_line_indexes):
            end_line = len(lines)
            if item_index + 1 < len(requirement_line_indexes):
                end_line = requirement_line_indexes[item_index + 1][0]
            collected: list[str] = []
            for current_index in range(line_index, min(end_line, line_index + 24)):
                line = lines[current_index].strip()
                if not line:
                    if collected:
                        break
                    continue
                if current_index > line_index and _is_xray_section_boundary(line):
                    break
                if current_index == line_index:
                    line = line[match.start():].strip()
                collected.append(line)
            segment_text = "\n".join(collected).strip()
            if not segment_text:
                segment_text = text
            segments.append(
                {
                    "requirement": _normalise_requirement_key(match.group("key")),
                    "text": segment_text[:2000],
                    "category": str(chunk.get("category") or "document"),
                    "source": str(chunk.get("source") or chunk.get("filename") or ""),
                }
            )
    # Preserve source order, but collapse duplicate requirement/body pairs.
    unique: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for segment in segments:
        identity = (segment["requirement"], segment["text"][:200])
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(segment)
    return unique


def _is_xray_section_boundary(line: str) -> bool:
    text = str(line or "").strip()
    if not text:
        return False
    if re.match(r"^page\s+\d+\b", text, flags=re.IGNORECASE):
        return True
    if re.match(r"^\d+\.\s+\S+", text):
        return True
    if text.lower() in {"id", "requirement", "acceptance criteria", "suggested id", "type", "coverage area", "expected result"}:
        return True
    return False


def _deterministic_xray_metadata_from_chunks(
    chunks: list[dict[str, Any]],
    tc_rows: list[dict[str, Any]],
) -> dict[str, dict[str, str]]:
    """Map existing automation cases to BRD requirement keys without LLM help."""
    segments = _requirement_segments_from_chunks(chunks)
    if not segments:
        return {}

    out: dict[str, dict[str, str]] = {}
    single_requirement = segments[0]["requirement"] if len({s["requirement"] for s in segments}) == 1 else ""
    for tc in tc_rows:
        title = str(tc.get("title") or "").strip()
        title_key = title.lower()
        if not title_key:
            continue
        steps_text = " ".join(str(step) for step in (tc.get("steps") or []))
        acceptance_text = " ".join(str(ac) for ac in (tc.get("acceptance_criteria") or []))
        scenario_text = str(tc.get("scenario_title") or "")
        case_text = " ".join([title, steps_text, acceptance_text, scenario_text])
        weighted_case_tokens = _weighted_xray_tokens(
            (title, 6),
            (acceptance_text, 4),
            (steps_text, 2),
            (scenario_text, 2),
        )
        best: tuple[int, dict[str, str]] | None = None
        for segment in segments:
            score = _score_requirement_segment(
                segment,
                case_text=case_text,
                weighted_case_tokens=weighted_case_tokens,
                title_text=title,
                single_requirement=bool(single_requirement),
            )
            if best is None or score > best[0]:
                best = (score, segment)
        if best and (best[0] > 0 or single_requirement):
            out[title_key] = {
                "labels": "Functional",
                "requirement": best[1]["requirement"],
                "priority": "High",
                "pre_condition_data": "Approved Phase 3 automation testcase",
                "_requirement_score": str(best[0]),
            }
    return out


def _merge_xray_metadata_fallback(
    primary: dict[str, dict[str, str]],
    fallback: dict[str, dict[str, str]],
) -> dict[str, dict[str, str]]:
    merged = dict(primary)
    for title_key, fallback_item in fallback.items():
        existing = dict(merged.get(title_key) or {})
        requirement = str(existing.get("requirement") or "").strip().upper()
        try:
            fallback_score = int(str(fallback_item.get("_requirement_score") or "0"))
        except ValueError:
            fallback_score = 0
        fallback_requirement = str(fallback_item.get("requirement") or "").strip()
        if (
            fallback_requirement
            and (
                not requirement
                or requirement == "TBD"
                or (
                    requirement != fallback_requirement.upper()
                    and fallback_score >= _XRAY_REQUIREMENT_OVERRIDE_SCORE
                )
            )
        ):
            existing["requirement"] = fallback_item.get("requirement", "")
        for field in ("labels", "priority", "pre_condition_data"):
            if not str(existing.get(field) or "").strip():
                existing[field] = fallback_item.get(field, "")
        merged[title_key] = existing
    return merged


def _assertion_evidence_source_text(item: dict[str, Any]) -> str:
    parts: list[str] = []
    parts.extend(str(step) for step in (item.get("steps") or []))
    parts.extend(str(ac) for ac in (item.get("acceptance_criteria") or []))
    parts.append(str(item.get("title") or ""))
    return " ".join(parts)


def _normalise_assertion_evidence(
    raw_items: list[dict[str, Any]],
    *,
    testcase_item: dict[str, Any],
    allowed_source_text: str = "",
) -> list[dict[str, Any]]:
    source_text = f"{_assertion_evidence_source_text(testcase_item)} {allowed_source_text}".lower()
    cleaned: list[dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("kind") or "").strip().lower()
        if kind not in _ASSERTION_EVIDENCE_KINDS:
            continue
        grounding = str(raw.get("grounding") or "").strip().lower()
        if grounding not in _ASSERTION_EVIDENCE_GROUNDING:
            grounding = "inferred"
        try:
            confidence = float(raw.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))

        outcome = str(raw.get("outcome") or "").strip()
        quoted_source = str(raw.get("source_text") or "").strip()
        observable_hint = raw.get("observable_hint")
        if isinstance(observable_hint, str):
            observable_hint = observable_hint.strip() or None
        elif observable_hint is not None:
            observable_hint = str(observable_hint).strip() or None

        # Do not trust unquoted/invented source text at high confidence.
        if quoted_source and quoted_source.lower() not in source_text:
            confidence = min(confidence, 0.49)
            grounding = "inferred"
        if not outcome or not quoted_source:
            confidence = min(confidence, 0.49)

        cleaned.append(
            {
                "kind": kind,
                "outcome": outcome,
                "source": str(raw.get("source") or "").strip() or "A3b",
                "source_text": quoted_source,
                "observable_hint": observable_hint,
                "confidence": round(confidence, 2),
                "grounding": grounding,
            }
        )
    return cleaned


def extract_assertion_evidence(
    *,
    item: dict[str, Any],
    hls_title: str,
    hls_description: str,
    document_context: str,
    recorded_steps: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    steps_text = "\n".join(f"  {idx + 1}. {step}" for idx, step in enumerate(item.get("steps") or []))
    ac_text = "\n".join(f"  - {ac}" for ac in (item.get("acceptance_criteria") or []))
    hls_context = f"{hls_title}\n{hls_description}".strip()
    recorded_context = _format_recorded_steps(recorded_steps or []) or "(no recorded evidence)"
    prompt = _ASSERTION_EVIDENCE_PROMPT.format(
        title=item.get("title") or "",
        target_page=item.get("target_page") or "",
        steps=steps_text or "  (no steps)",
        acceptance_criteria=ac_text or "  (none)",
        hls_context=hls_context or "(none)",
        document_context=document_context or "(none)",
        recorded_context=recorded_context,
    )
    for attempt in range(_MAX_LLM_RETRIES):
        try:
            raw = call_llm(prompt, max_tokens=3200)
            parsed = _parse_plan(raw)
            allowed_source = " ".join(
                [
                    hls_context,
                    document_context or "",
                    recorded_context,
                ]
            )
            return _normalise_assertion_evidence(
                parsed,
                testcase_item=item,
                allowed_source_text=allowed_source,
            )
        except Exception as exc:
            logger.warning(
                "agent3b evidence extraction attempt %d/%d failed for %r: %s",
                attempt + 1,
                _MAX_LLM_RETRIES,
                item.get("title"),
                exc,
            )
    return []


def plan_xray_metadata_for_cases(
    *,
    project_id: str,
    hls_items: list[tuple[str, str]],
    tc_rows: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, str]], dict[str, Any]]:
    """Enrich existing automation test cases with BRD/X-Ray metadata.

    This is the automation-first path: the same DB test cases used by A4/A5 are
    exported to CSV. Qdrant BRD chunks only add requirement/priority/label/setup
    metadata and must not create a separate manual testcase set.
    """
    diagnostics: dict[str, Any] = {
        "source": "BRD_QDRANT_METADATA",
        "chunks_found": 0,
        "rows_generated": 0,
        "fallback_reason": "",
    }
    title_keys = {str(tc.get("title") or "").strip().lower() for tc in tc_rows}
    if not title_keys:
        diagnostics["source"] = "A3_FALLBACK"
        diagnostics["fallback_reason"] = "no automation test cases available for CSV export"
        return {}, diagnostics

    try:
        batches, chunk_count, chunks = _xray_chunk_text_batches(project_id)
    except Exception as exc:
        diagnostics["source"] = "A3_FALLBACK"
        diagnostics["fallback_reason"] = f"Qdrant chunk lookup failed: {exc}"
        logger.warning(
            "agent3_xray: source=A3_FALLBACK project_id=%s reason=%s",
            project_id,
            diagnostics["fallback_reason"],
        )
        return {}, diagnostics

    diagnostics["chunks_found"] = chunk_count
    if not batches:
        diagnostics["source"] = "A3_FALLBACK"
        diagnostics["fallback_reason"] = "no BRD/FSD/WBS/assumption chunks found in Qdrant"
        logger.warning(
            "agent3_xray: source=A3_FALLBACK project_id=%s reason=%s",
            project_id,
            diagnostics["fallback_reason"],
        )
        return {}, diagnostics

    metadata_by_title: dict[str, dict[str, str]] = {}
    deterministic_metadata = _deterministic_xray_metadata_from_chunks(chunks, tc_rows)
    hls_text = _xray_hls_context(hls_items)
    tc_text = _xray_test_case_context(tc_rows)
    for batch_index, batch in enumerate(batches, 1):
        raw = call_llm(
            _XRAY_METADATA_PROMPT.format(
                hls_context=hls_text,
                test_case_context=tc_text,
                document_text=batch,
            ),
            max_tokens=2200,
        )
        for item in _parse_plan(raw):
            title_key = str(item.get("title") or "").strip().lower()
            if title_key not in title_keys or title_key in metadata_by_title:
                continue
            metadata_by_title[title_key] = _normalise_xray_metadata_item(item)
        logger.info(
            "agent3_xray: enriched metadata batch %d/%d project_id=%s rows_so_far=%d",
            batch_index,
            len(batches),
            project_id,
            len(metadata_by_title),
        )
        if len(metadata_by_title) >= len(title_keys):
            break

    metadata_by_title = _merge_xray_metadata_fallback(metadata_by_title, deterministic_metadata)
    diagnostics["rows_generated"] = len(metadata_by_title)
    if metadata_by_title:
        logger.info(
            "agent3_xray: source=BRD_QDRANT_METADATA project_id=%s chunks_found=%s rows_enriched=%s automation_cases=%s",
            project_id,
            chunk_count,
            len(metadata_by_title),
            len(title_keys),
        )
    else:
        diagnostics["source"] = "A3_FALLBACK"
        diagnostics["fallback_reason"] = "BRD/Qdrant metadata generation produced no matching automation case metadata"
        logger.warning(
            "agent3_xray: source=A3_FALLBACK project_id=%s chunks_found=%s reason=%s",
            project_id,
            chunk_count,
            diagnostics["fallback_reason"],
        )
    return metadata_by_title, diagnostics


# ── TC Document Generator ─────────────────────────────────────────────────────

def generate_tc_document(
    tc_rows: list[dict[str, Any]],
    project_name: str = "Project",
) -> str:
    """Legacy helper: produce a human-readable markdown TC document.

    New Phase 3 planning runs write X-Ray CSV instead. This remains available
    only for older callers/tests that still need a markdown rendering.

    tc_rows must include:
        tc_number, title, target_page, steps, acceptance_criteria,
        depends_on_titles (list[str]), hls_id, scenario_title

    This is shown in the Phase 3 UI before Execute and served as download.
    """
    from datetime import date
    from itertools import groupby

    lines: list[str] = [
        "# Test Case Document",
        "",
        f"**Project:** {project_name}  ",
        f"**Generated:** {date.today().isoformat()}  ",
        f"**Total test cases:** {len(tc_rows)}",
        "",
        "---",
        "",
    ]

    keyfn = lambda r: (r.get("hls_id", ""), r.get("scenario_title", "Scenario"))
    for (_, scenario_title), group in groupby(
        sorted(tc_rows, key=keyfn), key=keyfn
    ):
        lines.append(f"## Scenario — {scenario_title}")
        lines.append("")
        for tc in group:
            deps = tc.get("depends_on_titles", [])
            lines += [
                f"### {tc.get('tc_number', 'TC-???')} · {tc.get('title', 'Untitled')}",
                f"**Page:** `{tc.get('target_page', '')}`  ",
                f"**Depends on:** {', '.join(deps) if deps else '—'}  ",
                "",
                "**Steps**",
            ]
            for i, step in enumerate(tc.get("steps", []), 1):
                lines.append(f"{i}. {step}")
            lines += ["", "**Acceptance Criteria**"]
            for c in tc.get("acceptance_criteria", []):
                lines.append(f"- {c}")
            lines += ["", "---", ""]

    lines += [
        "## RTM Reference",
        "",
        "| Jira Bug | Test Case | Scenario |",
        "|----------|-----------|----------|",
        "| *(populated on bug raise)* | TC-00X | — |",
        "",
        "> Bug titles prefixed with TC number e.g. `[TC-003] Expected result not shown`  ",
        "> Traceability chain: Bug → Test Case → Scenario → Epic",
        "",
    ]
    return "\n".join(lines)


# ── Main entry point ──────────────────────────────────────────────────────────

async def plan(
    htc_title: str,
    htc_description: str,
    pages: list[str],
    project_id: str,
    hls_id: str = "",
    run_id: str = "",
    recorded_steps: list[dict[str, Any]] | None = None,
    tc_sequence_start: int = 1,
) -> list[str]:
    """Decompose one HLS into test cases. Returns list of persisted test_id strings.

    Args:
        tc_sequence_start: Global counter — callers must pass the correct start
                           value so TC numbers are unique across the whole run.
                           e.g. first HLS starts at 1, second starts at
                           1 + len(first_hls_test_ids), etc.
    """
    document_context_section = _format_planning_document_context(
        project_id,
        htc_title,
        htc_description,
    )
    prompt = _PLAN_PROMPT.format(
        htc_title=htc_title,
        htc_description=htc_description,
        pages_list="\n".join(f"  - {p}" for p in pages) or "  (no pages discovered yet)",
        document_context_section=document_context_section,
        recorded_steps_section=_format_recorded_steps(recorded_steps or []),
    )

    items: list[dict[str, Any]] = []
    for attempt in range(_MAX_LLM_RETRIES):
        try:
            raw   = await asyncio.to_thread(call_llm, prompt, max_tokens=3200)
            items = _parse_plan(raw)
            break
        except Exception as exc:
            logger.warning(
                "agent3 parse attempt %d/%d failed: %s",
                attempt + 1, _MAX_LLM_RETRIES, exc,
            )
            if attempt == _MAX_LLM_RETRIES - 1:
                logger.error("agent3 exhausted retries for HLS '%s'", htc_title)
                return []

    filtered_items, granularity_rejects = _valid_planned_items(
        items, pages, htc_title, htc_description, recorded_steps, document_context_section, project_id
    )
    if not filtered_items and granularity_rejects:
        logger.warning(
            "agent3: all generated cases were too granular for HLS %r; retrying once",
            htc_title,
        )
        try:
            raw = await asyncio.to_thread(call_llm, prompt + _GRANULARITY_RETRY_PROMPT, max_tokens=3200)
            items = _parse_plan(raw)
            filtered_items, _ = _valid_planned_items(
                items, pages, htc_title, htc_description, recorded_steps, document_context_section, project_id
            )
        except Exception as exc:
            logger.warning(
                "agent3 granularity retry failed for HLS '%s': %s",
                htc_title,
                exc,
            )
            return []

    # Pass 1 — validate + pre-assign UUIDs and tc_numbers
    valid_items:  list[tuple[dict, str, str]] = []
    title_to_id:  dict[str, str] = {}
    sequence = tc_sequence_start

    for item in filtered_items:
        test_id   = mcp_server.generate_id()
        tc_number = _make_tc_number(sequence)
        sequence += 1

        title_to_id[item["title"]] = test_id
        valid_items.append((item, test_id, tc_number))

    # Pass 2 — persist with resolved depends_on UUIDs
    test_ids: list[str] = []
    for item, test_id, tc_number in valid_items:
        raw_deps = item.get("depends_on", [])
        resolved_deps: list[str] = []
        unresolved: list[str] = []
        for d in raw_deps:
            if d in title_to_id:
                resolved_deps.append(title_to_id[d])
            else:
                unresolved.append(d)
        if unresolved:
            # The LLM emitted a depends_on title that doesn't match any title in
            # this HLS. Could be a typo / trailing punctuation / cross-HLS ref.
            # We DROP the dep (it can't be resolved to a UUID) but log loudly so
            # a human can correct A3's prompt or the input scenario.
            logger.warning(
                "agent3: %s '%s' has unresolved depends_on titles=%s "
                "(known titles in this HLS: %s)",
                tc_number, item["title"], unresolved, list(title_to_id.keys()),
            )

        mcp_server.save_test_case(
            test_id             = test_id,
            project_id          = project_id,
            hls_id              = hls_id,
            run_id              = run_id,
            tc_number           = tc_number,
            title               = item["title"],
            steps               = item["steps"],
            acceptance_criteria = item["acceptance_criteria"],
            depends_on          = resolved_deps,
            target_page         = item.get("target_page", ""),
            auth_mode           = _auth_mode_for_item(item),
            credential_role     = _infer_credential_role(
                project_id=project_id,
                title=item["title"],
                steps=item["steps"],
                hls_title=htc_title,
                hls_description=htc_description,
                document_context=document_context_section,
            ),
        )
        assertion_evidence: list[dict[str, Any]] = []
        try:
            assertion_evidence = extract_assertion_evidence(
                item=item,
                hls_title=htc_title,
                hls_description=htc_description,
                document_context=document_context_section,
                recorded_steps=recorded_steps,
            )
            if assertion_evidence:
                mcp_server.update_assertion_evidence(test_id, assertion_evidence)
            low_confidence = [
                ev for ev in assertion_evidence
                if float(ev.get("confidence") or 0.0) < 0.5
                or (ev.get("grounding") == "inferred" and float(ev.get("confidence") or 0.0) < 0.7)
            ]
            if assertion_evidence and len(low_confidence) == len(assertion_evidence):
                logger.warning(
                    "agent3b: low-confidence assertion evidence for %s %r; A5 may route to HUMAN_REVIEW",
                    tc_number,
                    item["title"],
                )
        except Exception as exc:
            logger.warning(
                "agent3b: evidence extraction failed for %s %r - continuing without evidence: %s",
                tc_number,
                item["title"],
                exc,
            )
        test_ids.append(test_id)
        logger.debug(
            "agent3: created %s test_id=%s title='%s'",
            tc_number, test_id, item["title"],
        )

    logger.info(
        "agent3: created %d test cases from HLS '%s' (TC-%03d → TC-%03d)",
        len(test_ids), htc_title, tc_sequence_start, sequence - 1,
    )
    return test_ids
