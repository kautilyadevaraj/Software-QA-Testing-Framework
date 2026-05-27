"""X-Ray CSV formatting utilities for Phase 3.

Agent 3 owns testcase planning. This module only renders exact X-Ray CSV output
and maps A3 automation rows into X-Ray import rows.
"""
from __future__ import annotations

import csv
import io
from typing import Any

XRAY_CSV_HEADERS = [
    "Project",
    "Issue_Type",
    "Labels",
    "Test Sets",
    "Reporter",
    "Test Type",
    "Requirement",
    "Priority",
    "Pre-Condition/Data",
    "TCID",
    "Summary",
    "Action",
    "Expected_Result",
]


def _clean_lines(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    return [line.strip() for line in text.splitlines() if line.strip()]


def _numbered_action(lines: list[str]) -> str:
    normalized: list[str] = []
    for index, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.lower().startswith("step "):
            normalized.append(stripped)
        elif stripped[:2].isdigit() and "." in stripped[:4]:
            normalized.append(stripped)
        else:
            normalized.append(f"{index}. {stripped}")
    return "\n".join(normalized)


def _expected_result(lines: list[str]) -> str:
    normalized: list[str] = []
    for line in lines:
        stripped = line.strip()
        normalized.append(stripped if stripped.startswith(">") else f">{stripped}")
    return "\n".join(normalized)


def _safe_cell(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def render_xray_csv(rows: list[dict[str, Any]]) -> str:
    """Render X-Ray rows with exact header order and CSV quoting."""
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=XRAY_CSV_HEADERS, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({header: row.get(header, "") for header in XRAY_CSV_HEADERS})
    return buffer.getvalue()


def fallback_xray_rows_from_a3(
    tc_rows: list[dict[str, Any]],
    *,
    project_key: str,
    reporter: str = "SQAT",
    test_set: str = "TBD",
    requirement: str = "TBD",
    metadata_by_title: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """Map existing A3 automation testcase rows into the X-Ray CSV shape."""
    rows: list[dict[str, str]] = []
    metadata_by_title = metadata_by_title or {}
    for index, tc in enumerate(tc_rows, 1):
        metadata = metadata_by_title.get(str(tc.get("title") or "").strip().lower(), {})
        steps = _clean_lines(tc.get("steps")) or ["Execute the approved Phase 3 testcase steps"]
        expected = _clean_lines(tc.get("acceptance_criteria")) or ["Expected result is observed"]
        rows.append(
            {
                "Project": project_key,
                "Issue_Type": "Test",
                "Labels": _safe_cell(metadata.get("labels"), "Functional"),
                "Test Sets": _safe_cell(metadata.get("test_set"), test_set),
                "Reporter": reporter,
                "Test Type": "Manual",
                "Requirement": _safe_cell(metadata.get("requirement"), requirement),
                "Priority": _safe_cell(metadata.get("priority"), "High"),
                "Pre-Condition/Data": _safe_cell(
                    metadata.get("pre_condition_data"),
                    "Approved Phase 3 automation testcase",
                ),
                "TCID": f"TC_{index:03d}",
                "Summary": _safe_cell(tc.get("title"), f"Generated test case {index}"),
                "Action": _numbered_action(steps),
                "Expected_Result": _expected_result(expected),
            }
        )
    return rows
