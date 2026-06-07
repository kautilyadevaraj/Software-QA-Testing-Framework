from __future__ import annotations

import logging
import os
import subprocess
import sys
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.phase3 import AuthState, TestCase
from app.models.project import CredentialProfile, Project
from app.services.credential_service import get_profile_password, list_project_profiles, normalize_auth_strategy
from app.services.artifact_paths import generated_base

logger = logging.getLogger(__name__)


class AuthStatePreparationError(RuntimeError):
    pass


def _server_dir() -> Path:
    return generated_base().parent.parent


def auth_state_path(project_id: str, run_id: str, credential_id: str) -> Path:
    return _server_dir() / "tests" / ".auth" / project_id / run_id / f"{credential_id}.json"


def _base_url(project: Project, profile: CredentialProfile | None = None) -> str:
    return (
        (profile.endpoint if profile and profile.endpoint else "")
        or project.url
        or settings.base_url
    ).rstrip("/")


def _role_matches(profile: CredentialProfile, requested_role: str | None) -> bool:
    if not requested_role:
        return False
    return (profile.role or "").strip().lower() == requested_role.strip().lower()


def profile_requires_storage_state(profile: CredentialProfile) -> bool:
    return normalize_auth_strategy(profile.auth_strategy, auth_type=profile.auth_type) == "storage_state"


def resolve_credential_bindings_for_run(
    db: Session,
    project_id: uuid.UUID,
    plan_run_id: uuid.UUID,
) -> list[CredentialProfile]:
    """Attach credential profiles to authenticated test cases for a plan run.

    This is the first production-safe step: credentials come from project CSV
    profiles, not .env. Role-aware selection can be made stricter later once A3
    emits role intent consistently.
    """
    profiles = list_project_profiles(db, project_id)
    if not profiles:
        return []

    default_profile = profiles[0] if len(profiles) == 1 else None
    tests = db.execute(
        select(TestCase).where(
            TestCase.project_id == project_id,
            TestCase.run_id == plan_run_id,
            TestCase.auth_mode.in_(["authenticated", "login_flow"]),
            TestCase.credential_id.is_(None),
        )
    ).scalars().all()

    for tc in tests:
        role_match = next((p for p in profiles if _role_matches(p, tc.credential_role)), None)
        profile = role_match or default_profile
        if profile is None:
            logger.warning(
                "auth_state: testcase %s has no credential_role in a multi-profile project; leaving unassigned",
                tc.test_id,
            )
            continue
        tc.credential_id = profile.id
        tc.credential_role = profile.role

    db.commit()
    return profiles


def assign_default_credentials_for_run(
    db: Session,
    project_id: uuid.UUID,
    plan_run_id: uuid.UUID,
) -> list[CredentialProfile]:
    """Backward-compatible alias for the explicit credential binding step."""
    return resolve_credential_bindings_for_run(db, project_id, plan_run_id)


def required_profiles_for_run(
    db: Session,
    project_id: uuid.UUID,
    plan_run_id: uuid.UUID,
) -> list[CredentialProfile]:
    credential_ids = db.execute(
        select(TestCase.credential_id)
        .where(
            TestCase.project_id == project_id,
            TestCase.run_id == plan_run_id,
            TestCase.auth_mode.in_(["authenticated", "login_flow"]),
            TestCase.credential_id.isnot(None),
        )
        .distinct()
    ).scalars().all()
    if not credential_ids:
        return []
    return list(
        db.execute(
            select(CredentialProfile).where(CredentialProfile.id.in_(credential_ids))
        )
        .scalars()
        .all()
    )


def run_auth_setup_for_profile(
    project: Project,
    run_id: uuid.UUID,
    profile: CredentialProfile,
) -> AuthState:
    """Run an explicit storage-state auth setup script for one profile."""
    server_dir = _server_dir()
    tests_dir = server_dir / "tests"
    script_name = (profile.auth_script or "auth.setup.ts").strip() or "auth.setup.ts"
    candidate = Path(script_name)
    if candidate.is_absolute() or ".." in candidate.parts:
        setup_file = tests_dir / "__invalid_auth_script__"
    else:
        setup_file = (tests_dir / candidate).resolve()
    dest = auth_state_path(str(project.id), str(run_id), str(profile.id))
    dest.parent.mkdir(parents=True, exist_ok=True)

    state = AuthState(
        id=uuid.uuid4(),
        project_id=project.id,
        run_id=run_id,
        credential_id=profile.id,
        storage_state_path=str(dest),
        status="pending",
    )

    if not profile_requires_storage_state(profile):
        state.status = "failed"
        state.error_message = (
            "AuthState setup is only valid for credential profiles with "
            "auth_strategy='storage_state'. Inline-login profiles do not need storageState."
        )
        return state

    if not str(setup_file).startswith(str(tests_dir.resolve())) or not setup_file.exists():
        state.status = "failed"
        state.error_message = f"auth setup script not found or not allowed: {script_name}"
        return state

    env = os.environ.copy()
    env["PLAYWRIGHT_HEADED"] = "true" if settings.playwright_headed else "false"
    env["PLAYWRIGHT_SLOW_MO_MS"] = str(settings.playwright_slow_mo_ms)
    env["BASE_URL"] = _base_url(project, profile)
    env["TEST_USERNAME"] = profile.username
    env["TEST_PASSWORD"] = get_profile_password(profile)
    env["USER_EMAIL"] = profile.username
    env["USER_PASSWORD"] = env["TEST_PASSWORD"]
    env["AUTH_STATE_PATH"] = str(dest)

    auth_config = server_dir / "playwright.auth.config.ts"
    try:
        npx_cmd = "npx.cmd" if sys.platform == "win32" else "npx"
        proc = subprocess.run(
            [npx_cmd, "playwright", "test", "--config", str(auth_config), "--reporter=dot"],
            capture_output=True,
            text=True,
            timeout=max(1, int(settings.auth_setup_timeout_s)),
            shell=False,
            env=env,
            cwd=str(server_dir),
        )
        if proc.returncode == 0 and dest.exists():
            state.status = "ready"
        else:
            state.status = "failed"
            state.error_message = (proc.stderr or proc.stdout or "auth setup failed")[:1000]
    except Exception as exc:
        state.status = "failed"
        state.error_message = str(exc)[:1000]

    return state


def prepare_auth_states_for_run(
    db: Session,
    project_id: str,
    execute_run_id: str,
    plan_run_id: str,
) -> dict[str, str]:
    """Create DB auth_state rows and return credential_id -> storage path."""
    project = db.get(Project, uuid.UUID(project_id))
    if not project:
        return {}

    plan_uuid = uuid.UUID(plan_run_id)
    execute_uuid = uuid.UUID(execute_run_id)
    assign_default_credentials_for_run(db, project.id, plan_uuid)
    profiles = required_profiles_for_run(db, project.id, plan_uuid)
    storage_profiles = [
        profile for profile in profiles
        if profile_requires_storage_state(profile)
    ]

    result: dict[str, str] = {}
    failed_profiles: list[str] = []
    for profile in storage_profiles:
        existing = db.execute(
            select(AuthState).where(
                AuthState.run_id == execute_uuid,
                AuthState.credential_id == profile.id,
            )
        ).scalar_one_or_none()
        if existing and existing.status == "ready" and Path(existing.storage_state_path).exists():
            result[str(profile.id)] = existing.storage_state_path
            continue

        if existing:
            db.delete(existing)
            db.commit()

        state = run_auth_setup_for_profile(project, execute_uuid, profile)
        db.add(state)
        db.commit()
        if state.status == "ready":
            result[str(profile.id)] = state.storage_state_path
        else:
            failed_profiles.append(f"{profile.role}:{profile.username}")
            logger.warning(
                "auth state failed: project_id=%s run_id=%s credential=%s error=%s",
                project_id,
                execute_run_id,
                profile.username,
                state.error_message,
            )

    required_ids = {str(profile.id) for profile in storage_profiles}
    missing = [credential_id for credential_id in required_ids if credential_id not in result]
    if missing:
        label = ", ".join(failed_profiles) if failed_profiles else ", ".join(missing)
        raise AuthStatePreparationError(
            "Phase 3 auth setup failed for required credential(s): "
            f"{label}. Re-verify project credentials before executing."
        )

    return result


def auth_state_map_for_run(db: Session, execute_run_id: str) -> dict[str, str]:
    rows = db.execute(
        select(AuthState).where(
            AuthState.run_id == uuid.UUID(execute_run_id),
            AuthState.status == "ready",
        )
    ).scalars().all()
    return {str(row.credential_id): row.storage_state_path for row in rows}
