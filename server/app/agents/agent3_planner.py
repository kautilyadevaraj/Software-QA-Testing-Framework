"""Agent A3 — Planner Agent.

Decomposes a High-Level Scenario (HLS) recorded in Phase 2 into concrete
test_case rows using the configured LLM.

Entry point:
    plan(htc_title, htc_description, pages, project_id,
         hls_id, recorded_steps, tc_sequence_start) -> list[str]

Changes from previous version:
- `acceptance_criteria` added to output schema  (CJ RTM requirement)
- `tc_number` (TC-001...) assigned and stored for Jira RTM traceability
- `hls_id` stored on every test case for UI grouping by scenario
- `generate_tc_document()` produces markdown shown in UI before Execute
- `tc_sequence_start` keeps TC numbers globally unique across all HLS in a run
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.services import mcp_server
from app.utils.llm import call_llm

logger = logging.getLogger(__name__)

_MAX_LLM_RETRIES = 3

# ── Prompt ────────────────────────────────────────────────────────────────────

_PLAN_PROMPT = """You are Agent A3, a senior QA test planning specialist.

Your job: decompose one High-Level Scenario (HLS) from Phase 2 into concrete,
executable test cases that a Playwright automation script can run directly.

═══ RULES ═══
1. Each test case targets exactly ONE page from the provided pages list.
2. Steps must be concise and action-oriented so a Playwright script can execute
   them without ambiguity.
   GOOD:  "click the Login button"
   BAD:   "the user should be able to log in"
3. Use ENV placeholder tokens for ALL sensitive values — NEVER hardcode:
     {{USER_EMAIL}}, {{USER_PASSWORD}}, {{BASE_URL}}
4. Do NOT invent pages not in the provided pages list.
5. If recorded tester actions are provided, derive your steps DIRECTLY from
   those actions. They are ground truth — real tester behaviour from Phase 2.
6. `depends_on` must contain EXACT title strings of other test cases in THIS
   response only, or be an empty array [].
7. `acceptance_criteria`: 2–4 short, verifiable pass conditions.
   Think: what would a QA manager check to sign off this test case?
8. Return ONLY a raw JSON array — no markdown fences, no explanation.

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
    "depends_on": [],
    "target_page": "/exact/page/path"
  }}
]

═══ FEW-SHOT EXAMPLE ═══
HLS Title: "User login and dashboard access"
Available pages: ["/", "/dashboard"]
Recorded steps:
  1. click  → selector: #username
  2. fill   → selector: #username → value: "admin@co.com"
  3. click  → selector: #password
  4. fill   → selector: #password → value: "secret123"
  5. click  → selector: button[type=submit]
  6. navigate → url: /dashboard

Expected output:
[
  {{
    "title": "Login with valid credentials",
    "steps": [
      "navigate to {{BASE_URL}}/",
      "fill #username with {{USER_EMAIL}}",
      "fill #password with {{USER_PASSWORD}}",
      "click the Login button",
      "wait for URL to contain /dashboard",
      "assert page heading is visible"
    ],
    "acceptance_criteria": [
      "URL redirects to /dashboard after login",
      "no 4xx or 5xx network responses during login",
      "dashboard heading visible on page"
    ],
    "depends_on": [],
    "target_page": "/"
  }},
  {{
    "title": "Dashboard loads with correct content",
    "steps": [
      "assert dashboard heading is visible",
      "assert at least one data widget is rendered",
      "assert no error banners on page"
    ],
    "acceptance_criteria": [
      "dashboard heading present",
      "data widgets render without loading spinners",
      "no error state shown"
    ],
    "depends_on": ["Login with valid credentials"],
    "target_page": "/dashboard"
  }}
]

═══ YOUR TASK ═══
HLS Title: {htc_title}
HLS Description: {htc_description}

Available pages:
{pages_list}
{recorded_steps_section}"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _format_recorded_steps(recorded_steps: list[dict[str, Any]]) -> str:
    if not recorded_steps:
        return ""
    capped = recorded_steps[:50]
    lines  = ["\nRecorded tester actions (ground truth — derive steps from these):"]
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


def _parse_plan(raw: str) -> list[dict[str, Any]]:
    cleaned = raw.strip()
    start   = cleaned.find("[")
    end     = cleaned.rfind("]")
    if start == -1 or end == -1:
        raise ValueError("No JSON array found in LLM response")
    return json.loads(cleaned[start: end + 1])


def _make_tc_number(sequence: int) -> str:
    return f"TC-{sequence:03d}"


# ── TC Document Generator ─────────────────────────────────────────────────────

def generate_tc_document(
    tc_rows: list[dict[str, Any]],
    project_name: str = "Project",
) -> str:
    """Produce a human-readable markdown TC document from persisted test case rows.

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
        "> Bug titles prefixed with TC number e.g. `[TC-003] Cart total wrong`  ",
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
    prompt = _PLAN_PROMPT.format(
        htc_title=htc_title,
        htc_description=htc_description,
        pages_list="\n".join(f"  - {p}" for p in pages) or "  (no pages discovered yet)",
        recorded_steps_section=_format_recorded_steps(recorded_steps or []),
    )

    items: list[dict[str, Any]] = []
    for attempt in range(_MAX_LLM_RETRIES):
        try:
            raw   = call_llm(prompt, max_tokens=1200)
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

    # Pass 1 — validate + pre-assign UUIDs and tc_numbers
    valid_items:  list[tuple[dict, str, str]] = []
    title_to_id:  dict[str, str] = {}
    sequence = tc_sequence_start

    for item in items:
        target_page = item.get("target_page", "")
        if pages and target_page not in pages:
            logger.warning(
                "agent3: target_page '%s' not in pages list — skipping", target_page
            )
            continue
        if not item.get("title") or not item.get("steps"):
            continue
        # Guarantee acceptance_criteria is always present
        if not item.get("acceptance_criteria"):
            item["acceptance_criteria"] = ["Test completes without errors"]

        test_id   = mcp_server.generate_id()
        tc_number = _make_tc_number(sequence)
        sequence += 1

        title_to_id[item["title"]] = test_id
        valid_items.append((item, test_id, tc_number))

    # Pass 2 — persist with resolved depends_on UUIDs
    test_ids: list[str] = []
    for item, test_id, tc_number in valid_items:
        raw_deps      = item.get("depends_on", [])
        resolved_deps = [title_to_id[d] for d in raw_deps if d in title_to_id]

        mcp_server.save_test_case(
            test_id             = test_id,
            project_id          = project_id,
            hls_id              = hls_id,
            tc_number           = tc_number,
            title               = item["title"],
            steps               = item["steps"],
            acceptance_criteria = item["acceptance_criteria"],
            depends_on          = resolved_deps,
            target_page         = item.get("target_page", ""),
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
