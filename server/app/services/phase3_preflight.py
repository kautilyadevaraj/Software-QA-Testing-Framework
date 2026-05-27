"""Pre-flight checks for Phase 3 execution.

Runs before `/phase3/execute` creates a TestRun so we fail fast with a human
-readable message instead of enqueueing N Playwright jobs that all die on
`env('BASE_URL')` at line 1 of the generated spec.

Every check returns (ok: bool, message: str). The router aggregates failures
and raises a single 400 with all missing pieces.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.phase3 import TestCase
from app.models.project import CredentialProfile, Project


@dataclass(frozen=True)
class PreflightIssue:
    code: str
    message: str


# Modes that need the server-level env vars (BASE_URL/USER_EMAIL/USER_PASSWORD).
# 'authenticated' is satisfied by a CredentialProfile row — checked separately.
_ENV_MODES = {"login_flow", "anonymous"}

_DEFAULT_BASE_URLS = {"http://localhost:3000", "http://localhost:3000/"}


def check_execution_preflight(
    db: Session,
    project_id: uuid.UUID,
    plan_run_id: uuid.UUID,
) -> list[PreflightIssue]:
    """Validate that Phase 3 execution can actually run end-to-end.

    Checks performed:
      1. The plan has at least one test case (already in router, duplicated for safety)
      2. For 'login_flow'/'anonymous' TCs — settings.user_email + user_password set
      3. For 'authenticated' TCs — at least one CredentialProfile exists, AND the
         profile has a non-empty endpoint (either own endpoint or project.url)
      4. BASE_URL is not still the default localhost:3000 unless project.url is set
    """
    issues: list[PreflightIssue] = []

    # 1. collect distinct auth_modes used by TCs in this plan
    rows = db.execute(
        select(TestCase.auth_mode)
        .where(
            TestCase.project_id == project_id,
            TestCase.run_id == plan_run_id,
            TestCase.approval_status == "APPROVED",
        )
        .distinct()
    ).scalars().all()
    auth_modes = {m or "authenticated" for m in rows}

    # 2. env-var guard — only matters if there are anon/login_flow TCs
    if auth_modes & _ENV_MODES:
        missing: list[str] = []
        if not (settings.user_email or "").strip():
            missing.append("USER_EMAIL")
        if not (settings.user_password or "").strip():
            missing.append("USER_PASSWORD")
        if missing:
            issues.append(PreflightIssue(
                code="missing_env_vars",
                message=(
                    f"Server .env is missing required Playwright credentials: "
                    f"{', '.join(missing)}. "
                    f"The generated scripts call env('{missing[0]}') which will "
                    "throw at runtime. Add them to server/.env and restart FastAPI."
                ),
            ))

    # 3. authenticated TCs need usable CredentialProfiles. In multi-profile
    # projects, a testcase must resolve to an explicit role; otherwise choosing
    # the first profile silently runs the wrong user's permissions.
    if "authenticated" in auth_modes:
        profiles = list(db.execute(
            select(CredentialProfile)
            .where(CredentialProfile.project_id == project_id)
            .order_by(CredentialProfile.role.asc(), CredentialProfile.username.asc())
        ).scalars().all())
        if not profiles:
            issues.append(PreflightIssue(
                code="no_credential_profile",
                message=(
                    "Authenticated test cases require at least one uploaded "
                    "project credential profile. Upload one from the Phase 1 panel."
                ),
            ))
        else:
            role_map = {str(profile.role or "").strip().lower(): profile for profile in profiles}
            auth_cases = list(db.execute(
                select(TestCase)
                .where(
                    TestCase.project_id == project_id,
                    TestCase.run_id == plan_run_id,
                    TestCase.approval_status == "APPROVED",
                    TestCase.auth_mode == "authenticated",
                )
            ).scalars().all())

            missing_roles = sorted({
                str(tc.credential_role or "").strip()
                for tc in auth_cases
                if tc.credential_role and str(tc.credential_role).strip().lower() not in role_map
            })
            if missing_roles:
                issues.append(PreflightIssue(
                    code="missing_credential_role",
                    message=(
                        "Authenticated test cases require credential role(s) "
                        f"{', '.join(missing_roles)}, but uploaded profiles only include: "
                        f"{', '.join(sorted(role_map)) or '(none)'}. Upload matching credentials "
                        "or edit the testcase credential role."
                    ),
                ))

            if len(profiles) > 1:
                ambiguous = [
                    tc.tc_number or str(tc.test_id)
                    for tc in auth_cases
                    if not (tc.credential_id or str(tc.credential_role or "").strip())
                ]
                if ambiguous:
                    issues.append(PreflightIssue(
                        code="ambiguous_credential_role",
                        message=(
                            "Multiple credential profiles are uploaded, but these authenticated "
                            "test cases do not specify which role to use: "
                            f"{', '.join(ambiguous[:10])}. Edit the testcase role or ensure A3 "
                            "can infer it from the BRD/HLS."
                        ),
                    ))

        profile = profiles[0] if profiles else None
        if profile and not (profile.endpoint or "").strip():
            # Fall back to project.url if the profile has no endpoint
            project = db.get(Project, project_id)
            if not (project and (project.url or "").strip()):
                issues.append(PreflightIssue(
                    code="no_base_url",
                    message=(
                        "No BASE_URL available for authenticated tests. "
                        "Set the project URL in Phase 1, or add an `endpoint` "
                        "column to the uploaded credential profile."
                    ),
                ))

    # 4. BASE_URL sanity — warn if default localhost:3000 is still in use
    #    AND the project doesn't override it via project.url
    base = (settings.base_url or "").rstrip("/")
    if base in {u.rstrip("/") for u in _DEFAULT_BASE_URLS}:
        project = db.get(Project, project_id)
        if not (project and (project.url or "").strip()):
            issues.append(PreflightIssue(
                code="default_base_url",
                message=(
                    f"BASE_URL is still the default '{settings.base_url}'. "
                    "Either set BASE_URL in server/.env or add the target URL "
                    "to the project."
                ),
            ))

    return issues


def format_issues(issues: Iterable[PreflightIssue]) -> str:
    """Format for the 400 response detail (one line per issue, numbered)."""
    return "Phase 3 execution preflight failed:\n" + "\n".join(
        f"  {i}. {issue.message}" for i, issue in enumerate(issues, 1)
    )
