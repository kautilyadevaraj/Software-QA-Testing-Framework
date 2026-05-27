from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Literal

Phase3JobType = Literal["hls_group", "single_test"]


def build_hls_group_job(
    *,
    project_id: str,
    run_id: str,
    plan_run_id: str | None,
    hls_id: str,
    script_path: str,
    ordered_test_ids: list[str],
    credential_id: str | None = None,
    storage_state_path: str | None = None,
    attempt: int = 1,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "job_id": str(uuid.uuid4()),
        "job_type": "hls_group",
        "project_id": project_id,
        "run_id": run_id,
        "plan_run_id": plan_run_id,
        "hls_id": hls_id,
        "script_path": script_path,
        "ordered_test_ids": ordered_test_ids,
        "credential_id": credential_id,
        "storage_state_path": storage_state_path,
        "attempt": attempt,
    }


def build_single_test_job(
    *,
    project_id: str | None,
    run_id: str,
    test_id: str,
    script_path: str,
    review_item_id: str | None = None,
    storage_state_path: str | None = None,
    attempt: int = 1,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "job_id": str(uuid.uuid4()),
        "job_type": "single_test",
        "project_id": project_id,
        "run_id": run_id,
        "test_id": test_id,
        "script_path": script_path,
        "review_item_id": review_item_id,
        "storage_state_path": storage_state_path,
        "attempt": attempt,
    }


def serialize_job(job: dict[str, Any]) -> str:
    return json.dumps(job, separators=(",", ":"), sort_keys=True)


def parse_job(message: str) -> dict[str, Any] | None:
    try:
        job = json.loads(message)
    except json.JSONDecodeError:
        return None
    if not isinstance(job, dict):
        return None
    if job.get("schema_version") != 1:
        return None
    if job.get("job_type") not in {"hls_group", "single_test"}:
        return None
    return job


def job_script_path(job: dict[str, Any]) -> Path:
    value = str(job.get("script_path") or "")
    if not value:
        raise ValueError("job missing script_path")
    return Path(value)
