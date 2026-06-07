"""Pre-flight checks for Phase 3 execution.

Runs before `/phase3/execute` creates a TestRun so we fail fast with a human
readable message instead of enqueueing Playwright jobs with missing credentials
or a default localhost BASE_URL.
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


_PROJECT_CREDENTIAL_MODES = {"authenticated", "login_flow"}
_DEFAULT_BASE_URLS = {"http://localhost:3000", "http://localhost:3000/"}


def check_execution_preflight(
    db: Session,
    project_id: uuid.UUID,
    plan_run_id: uuid.UUID,
) -> list[PreflightIssue]:
    """Validate that Phase 3 execution can actually run end-to-end."""
    issues: list[PreflightIssue] = []

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

    if auth_modes & _PROJECT_CREDENTIAL_MODES:
        profiles = list(db.execute(
            select(CredentialProfile)
            .where(CredentialProfile.project_id == project_id)
            .order_by(CredentialProfile.role.asc(), CredentialProfile.username.asc())
        ).scalars().all())
        if not profiles:
            issues.append(PreflightIssue(
                code="no_credential_profile",
                message=(
                    "Credentialed Phase 3 test cases require at least one uploaded "
                    "project credential profile. Upload credentials from the Phase 1 panel."
                ),
            ))
        else:
            from app.services.auth_state_service import resolve_credential_bindings_for_run

            resolve_credential_bindings_for_run(db, project_id, plan_run_id)
            role_map = {str(profile.role or "").strip().lower(): profile for profile in profiles}
            auth_cases = list(db.execute(
                select(TestCase)
                .where(
                    TestCase.project_id == project_id,
                    TestCase.run_id == plan_run_id,
                    TestCase.approval_status == "APPROVED",
                    TestCase.auth_mode.in_(_PROJECT_CREDENTIAL_MODES),
                )
            ).scalars().all())

            unbound = [
                tc.tc_number or str(tc.test_id)
                for tc in auth_cases
                if not tc.credential_id
            ]
            if unbound:
                issues.append(PreflightIssue(
                    code="unbound_credential",
                    message=(
                        "These approved credentialed test cases have no bound project credential: "
                        f"{', '.join(unbound[:10])}. Re-plan Phase 3 or edit the test case "
                        "credential role before executing."
                    ),
                ))

            missing_roles = sorted({
                str(tc.credential_role or "").strip()
                for tc in auth_cases
                if tc.credential_role and str(tc.credential_role).strip().lower() not in role_map
            })
            if missing_roles:
                issues.append(PreflightIssue(
                    code="missing_credential_role",
                    message=(
                        "Credentialed test cases require credential role(s) "
                        f"{', '.join(missing_roles)}, but uploaded profiles only include: "
                        f"{', '.join(sorted(role_map)) or '(none)'}. Upload matching credentials "
                        "or edit the testcase credential role."
                    ),
                ))

            if len(profiles) > 1:
                ambiguous = [
                    tc.tc_number or str(tc.test_id)
                    for tc in auth_cases
                    if not str(tc.credential_role or "").strip()
                ]
                if ambiguous:
                    issues.append(PreflightIssue(
                        code="ambiguous_credential_role",
                        message=(
                            "Multiple credential profiles are uploaded, but these credentialed "
                            "test cases do not specify which role to use: "
                            f"{', '.join(ambiguous[:10])}. Edit the testcase role or ensure A3 "
                            "can infer it from the BRD/HLS."
                        ),
                    ))

        profile = profiles[0] if profiles else None
        if profile and not (profile.endpoint or "").strip():
            project = db.get(Project, project_id)
            if not (project and (project.url or "").strip()):
                issues.append(PreflightIssue(
                    code="no_base_url",
                    message=(
                        "No BASE_URL available for credentialed tests. "
                        "Set the project URL in Phase 1, or add an `endpoint` "
                        "column to the uploaded credential profile."
                    ),
                ))

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
    return "Phase 3 execution preflight failed:\n" + "\n".join(
        f"  {i}. {issue.message}" for i, issue in enumerate(issues, 1)
    )
