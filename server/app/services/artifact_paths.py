"""Filesystem layout helpers for Phase 3 artifacts.

New artifacts use a run-scoped, human-readable layout:

    tests/generated/<project_id>/<run_id>/
      scripts/TC-001__login-flow__<test_id_short>.spec.ts
      traces/
      documents/xray_testcases.csv
      manifest.json

The helpers return absolute paths so subprocess cwd changes cannot accidentally
nest `tests/generated` inside itself.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings

_SERVER_ROOT = Path(__file__).resolve().parents[2]
_SLUG_RE = re.compile(r"[^a-zA-Z0-9]+")


def generated_base() -> Path:
    base = Path(settings.generated_scripts_dir)
    if not base.is_absolute():
        base = _SERVER_ROOT / base
    base.mkdir(parents=True, exist_ok=True)
    return base


def short_id(value: str | uuid.UUID | None, length: int = 8) -> str:
    raw = str(value or "").replace("-", "")
    return raw[:length] or "unknown"


def slugify(value: str, *, max_len: int = 60) -> str:
    slug = _SLUG_RE.sub("-", value.strip().lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return (slug[:max_len].strip("-") or "untitled")


def run_dir(project_id: str, run_id: str) -> Path:
    path = generated_base() / str(project_id) / str(run_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def scripts_dir(project_id: str, run_id: str) -> Path:
    path = run_dir(project_id, run_id) / "scripts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def traces_dir_from_script(script_path: Path) -> Path:
    parent = script_path.parent
    path = parent.parent / "traces" if parent.name == "scripts" else parent / "traces"
    path.mkdir(parents=True, exist_ok=True)
    return path


def documents_dir(project_id: str, run_id: str) -> Path:
    path = run_dir(project_id, run_id) / "documents"
    path.mkdir(parents=True, exist_ok=True)
    return path


def tc_document_path(project_id: str, run_id: str) -> Path:
    return documents_dir(project_id, run_id) / "xray_testcases.csv"


def legacy_tc_document_path(run_id: str) -> Path:
    return generated_base() / f"tc_document_{run_id}.csv"


def testcase_script_name(
    test_id: str,
    *,
    tc_number: str | None = None,
    title: str | None = None,
) -> str:
    prefix = (tc_number or "TC").replace("_", "-")
    return f"{prefix}__{slugify(title or 'testcase')}__{short_id(test_id)}.spec.ts"


def manifest_path(project_id: str, run_id: str) -> Path:
    return run_dir(project_id, run_id) / "manifest.json"


def upsert_manifest_entry(
    project_id: str,
    run_id: str,
    entry: dict[str, Any],
) -> None:
    path = manifest_path(project_id, run_id)
    try:
        manifest = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        manifest = {}
    entries = manifest.get("test_cases") or {}
    test_id = str(entry.get("test_id") or "")
    if test_id:
        entries[test_id] = entry
    manifest.update({
        "project_id": str(project_id),
        "run_id": str(run_id),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "test_cases": entries,
    })
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    try:
        from app.services.artifact_registry import register_artifact

        register_artifact(
            project_id=project_id,
            run_id=run_id,
            artifact_type="MANIFEST",
            path=path,
        )
    except Exception:
        # Manifest is a convenience index; artifact registration is best-effort
        # because callers must not fail after a successful script write.
        pass
