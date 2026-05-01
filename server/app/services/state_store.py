"""In-process state cache backed by a JSON file.

All workers run as threads in the same process, so a threading.Lock is
sufficient to prevent concurrent write corruption. The file path is
configured via settings.state_json_path.
"""
from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from typing import Any

from app.core.config import settings

_lock = threading.Lock()


def _path() -> Path:
    return Path(settings.state_json_path)


def _load() -> dict[str, Any]:
    p = _path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save(state: dict[str, Any]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, default=str, indent=2), encoding="utf-8")


# ── Public API ──────────────────────────────────────────────────────────────


def init_test(test_id: str | uuid.UUID) -> None:
    """Register a new test entry with status PENDING."""
    key = str(test_id)
    with _lock:
        state = _load()
        if key not in state:
            state[key] = {
                "status": "PENDING",
                "retries": 0,
                "jira_ticket": None,
                "trace_path": None,
                "network_logs": [],
            }
            _save(state)


def update_state(test_id: str | uuid.UUID, status: str, **extras: Any) -> None:
    """Atomically update a test's status and any additional fields."""
    key = str(test_id)
    with _lock:
        state = _load()
        entry = state.setdefault(
            key,
            {"status": "PENDING", "retries": 0, "jira_ticket": None, "trace_path": None, "network_logs": []},
        )
        entry["status"] = status
        for k, v in extras.items():
            entry[k] = v
        _save(state)


def increment_retries(test_id: str | uuid.UUID) -> int:
    """Increment retry count atomically. Returns the new count."""
    key = str(test_id)
    with _lock:
        state = _load()
        entry = state.setdefault(key, {"status": "PENDING", "retries": 0, "jira_ticket": None, "trace_path": None, "network_logs": []})
        entry["retries"] = entry.get("retries", 0) + 1
        _save(state)
        return entry["retries"]


def get_status(test_id: str | uuid.UUID) -> dict[str, Any] | None:
    """Return the state entry for a test, or None if not found."""
    key = str(test_id)
    with _lock:
        return _load().get(key)


def get_retry_count(test_id: str | uuid.UUID) -> int:
    entry = get_status(test_id)
    return entry.get("retries", 0) if entry else 0


def append_network_log(test_id: str | uuid.UUID, log: dict[str, Any]) -> None:
    """Append a network log entry for a test."""
    key = str(test_id)
    with _lock:
        state = _load()
        entry = state.setdefault(key, {"status": "PENDING", "retries": 0, "jira_ticket": None, "trace_path": None, "network_logs": []})
        entry.setdefault("network_logs", []).append(log)
        _save(state)


def init_hls_group(hls_id: str, ordered_test_ids: list[str]) -> None:
    """Store ordered list of test_ids for a grouped HLS spec.

    Workers look this up after running {hls_id}.spec.ts to assign per-subtask
    TestResult rows by position (not by title) to avoid LLM title-mismatch bugs.
    Prefixed 'hls:' so get_all() and flush skip it.
    """
    key = f"hls:{hls_id}"
    with _lock:
        state = _load()
        state[key] = {
            "type": "hls_group",
            "ordered_test_ids": ordered_test_ids,
        }
        _save(state)


def get_hls_group(hls_id: str) -> list[str] | None:
    """Return the ordered list of test_ids for an HLS group, or None."""
    key = f"hls:{hls_id}"
    with _lock:
        entry = _load().get(key)
        if entry and entry.get("type") == "hls_group":
            return entry.get("ordered_test_ids", [])
        return None


def get_all() -> dict[str, Any]:
    """Return a snapshot of the entire state (thread-safe read), excluding HLS metadata."""
    with _lock:
        return {k: v for k, v in _load().items() if not k.startswith("hls:")}


def clear() -> None:
    """Delete the state file. Called after a successful flush to PostgreSQL."""
    with _lock:
        p = _path()
        if p.exists():
            p.unlink()


# Alias used by the reset endpoint for semantic clarity
clear_all = clear
