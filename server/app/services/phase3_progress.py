"""In-memory progress tracker for Phase 3 runs.

Purpose
-------
Between clicking Execute and the first test_result landing in the DB, there's
a 60-90s window where A4 (context builder) + A5 (script generator) run serially
per HLS. The UI shows "running" but no detail — users think it's frozen.

This module stores a lightweight progress note per run_id so the /run-status
endpoint can return strings like:
    "A5: generating script for HLS 2/5 — 'User workflow'"
    "A4: building context for TC-007 in 'User login'"

Why in-memory
-------------
- No DB migration for a demo-only feature
- Runs don't survive process restart anyway (BackgroundTask is in-process)
- Thread-safe via dict-level writes (GIL) + explicit lock for compound ops

Scale caveat
------------
Multi-pod FastAPI deployments will have inconsistent progress visible
depending on which pod the polling request hits. Accept this for demo;
move to Redis if needed later.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field, asdict

_lock = threading.Lock()
_progress: dict[str, "RunProgress"] = {}

# Stage labels shown verbatim in the UI
STAGE_PLANNING_A3      = "planning"         # A3 decomposing HLS → TCs
STAGE_EXEC_PREFLIGHT   = "preflight"        # auth bootstrap / env setup
STAGE_EXEC_A4          = "building_context" # A4 DOM snapshot + context
STAGE_EXEC_A5          = "generating_script"# A5 LLM script generation
STAGE_EXEC_QUEUING     = "queuing"          # enqueueing Playwright jobs
STAGE_EXEC_RUNNING     = "running_tests"    # workers executing specs
STAGE_EXEC_DONE        = "done"


@dataclass
class RunProgress:
    run_id: str
    stage: str
    message: str = ""
    # HLS progress counters (0-indexed → UI renders as i+1 of N)
    current_hls_index: int | None = None
    total_hls: int | None = None
    current_hls_title: str = ""
    # Test progress counters within a running HLS
    current_test_id: str | None = None
    current_test_title: str = ""
    updated_at: float = field(default_factory=time.time)


def start_run(run_id: str, total_hls: int = 0, stage: str = STAGE_PLANNING_A3) -> None:
    with _lock:
        _progress[run_id] = RunProgress(
            run_id=run_id, stage=stage, total_hls=total_hls,
        )


def set_stage(
    run_id: str,
    stage: str,
    *,
    message: str = "",
    current_hls_index: int | None = None,
    total_hls: int | None = None,
    current_hls_title: str = "",
    current_test_id: str | None = None,
    current_test_title: str = "",
) -> None:
    """Update the stage and optional context details. Creates entry if missing."""
    with _lock:
        p = _progress.get(run_id) or RunProgress(run_id=run_id, stage=stage)
        p.stage = stage
        if message:
            p.message = message
        if current_hls_index is not None:
            p.current_hls_index = current_hls_index
        if total_hls is not None:
            p.total_hls = total_hls
        if current_hls_title:
            p.current_hls_title = current_hls_title
        if current_test_id is not None:
            p.current_test_id = current_test_id
        if current_test_title:
            p.current_test_title = current_test_title
        p.updated_at = time.time()
        _progress[run_id] = p


def get_progress(run_id: str) -> dict | None:
    """Return a JSON-serializable snapshot of the run progress, or None."""
    p = _progress.get(run_id)
    if not p:
        return None
    d = asdict(p)
    # Human-readable helper for the UI
    d["headline"] = _render_headline(p)
    return d


def clear_run(run_id: str) -> None:
    with _lock:
        _progress.pop(run_id, None)


def _render_headline(p: RunProgress) -> str:
    """Build a one-line summary the UI can display verbatim."""
    if p.stage == STAGE_PLANNING_A3:
        base = "Generating test cases"
        if p.current_hls_index is not None and p.total_hls:
            base += f" — HLS {p.current_hls_index + 1}/{p.total_hls}"
            if p.current_hls_title:
                base += f": {p.current_hls_title[:50]}"
        return base
    if p.stage == STAGE_EXEC_PREFLIGHT:
        return "Preparing authentication"
    if p.stage == STAGE_EXEC_A4:
        if p.current_hls_title:
            return f"Building context — HLS {(p.current_hls_index or 0) + 1}/{p.total_hls or '?'}: {p.current_hls_title[:50]}"
        return "Building test context"
    if p.stage == STAGE_EXEC_A5:
        if p.current_hls_title:
            return f"Generating script — HLS {(p.current_hls_index or 0) + 1}/{p.total_hls or '?'}: {p.current_hls_title[:50]}"
        return "Generating Playwright scripts"
    if p.stage == STAGE_EXEC_QUEUING:
        return "Queuing Playwright jobs"
    if p.stage == STAGE_EXEC_RUNNING:
        if p.current_test_title:
            return f"Running: {p.current_test_title[:60]}"
        return "Running Playwright tests"
    if p.stage == STAGE_EXEC_DONE:
        return "Run complete"
    return p.message or p.stage
