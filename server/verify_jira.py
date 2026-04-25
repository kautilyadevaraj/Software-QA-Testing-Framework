"""verify_jira.py — Dev utility: cross-check local DB tickets against Jira.

Checks every row in the `jira_tickets` table and verifies it actually exists
on the Jira board. Reports:
  ✔  MATCHED  — ticket exists in both DB and Jira
  ✘  MISSING  — ticket is in DB but NOT found on Jira (deleted externally?)
  ⚠  ERROR    — Jira API call failed (auth error, network issue, etc.)

Also verifies every `project_jira_config` row by checking if the Jira project
still exists.

Usage (from server/ with venv activated):
    python verify_jira.py
"""

from __future__ import annotations

import sys
import os

# ── Make sure app imports work when run from server/ directly ────────────────
sys.path.insert(0, os.path.dirname(__file__))

import requests
from requests.auth import HTTPBasicAuth
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.project import JiraTicket, ProjectJiraConfig
from app.models.user import User  # noqa: F401 — needed for relationship resolution
from app.models.project import Project  # noqa: F401

# ── Colours for terminal output ──────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

TICK   = f"{GREEN}✔{RESET}"
CROSS  = f"{RED}✘{RESET}"
WARN   = f"{YELLOW}⚠{RESET}"
INFO   = f"{CYAN}ℹ{RESET}"


# ── Jira helpers ─────────────────────────────────────────────────────────────

def _auth() -> HTTPBasicAuth:
    s = get_settings()
    return HTTPBasicAuth(s.jira_email or "", s.jira_api_token or "")


def _base() -> str:
    return (get_settings().jira_base_url or "").rstrip("/")


def _headers() -> dict[str, str]:
    return {"Accept": "application/json"}


def jira_issue_exists(issue_key: str) -> tuple[bool, str | None]:
    """Return (exists, summary|None).

    Returns (False, None) when the issue is not found (404).
    Returns (False, error_msg) on any other API error.
    """
    url = f"{_base()}/rest/api/3/issue/{issue_key}"
    try:
        resp = requests.get(url, auth=_auth(), headers=_headers(), timeout=10)
        if resp.status_code == 200:
            return True, resp.json().get("fields", {}).get("summary", "")
        if resp.status_code == 404:
            return False, None
        return False, f"HTTP {resp.status_code}: {resp.text[:120]}"
    except requests.RequestException as exc:
        return False, f"Network error: {exc}"


def jira_project_exists(project_key: str) -> tuple[bool, str | None]:
    """Return (exists, project_name|None)."""
    url = f"{_base()}/rest/api/3/project/{project_key}"
    try:
        resp = requests.get(url, auth=_auth(), headers=_headers(), timeout=10)
        if resp.status_code == 200:
            return True, resp.json().get("name", "")
        if resp.status_code == 404:
            return False, None
        return False, f"HTTP {resp.status_code}: {resp.text[:120]}"
    except requests.RequestException as exc:
        return False, f"Network error: {exc}"


# ── Main verification ─────────────────────────────────────────────────────────

def verify(db: Session) -> None:
    settings = get_settings()

    # ── Pre-flight checks ────────────────────────────────────────────────────
    print(f"\n{BOLD}═══ SQAT Jira Verification ═══{RESET}\n")

    if not all([settings.jira_base_url, settings.jira_email,
                settings.jira_api_token, settings.jira_lead_account_id]):
        print(f"{CROSS} Jira credentials are not fully configured in .env — aborting.\n")
        return

    print(f"{INFO} Jira workspace : {settings.jira_base_url}")
    print(f"{INFO} Jira account   : {settings.jira_email}\n")

    # ── Section 1: Jira project configs ─────────────────────────────────────
    print(f"{BOLD}── Project Jira Configs ──────────────────────────────────{RESET}")
    configs = db.query(ProjectJiraConfig).all()

    if not configs:
        print(f"  {INFO} No project_jira_config rows found.\n")
    else:
        matched = 0
        missing = 0
        for cfg in configs:
            exists, name_or_err = jira_project_exists(cfg.jira_project_key)
            project = db.query(Project).filter(Project.id == cfg.project_id).first()
            app_name = project.name if project else f"<deleted project {cfg.project_id}>"

            if exists:
                matched += 1
                print(f"  {TICK} [{cfg.jira_project_key}] App project: '{app_name}' → Jira: '{name_or_err}'")
            elif name_or_err is None:
                missing += 1
                print(f"  {CROSS} [{cfg.jira_project_key}] App project: '{app_name}' → NOT FOUND on Jira")
            else:
                print(f"  {WARN} [{cfg.jira_project_key}] App project: '{app_name}' → {name_or_err}")

        print(f"\n  Summary: {matched} matched, {missing} missing from Jira\n")

    # ── Section 2: Jira tickets ──────────────────────────────────────────────
    print(f"{BOLD}── Jira Tickets ──────────────────────────────────────────{RESET}")
    tickets = db.query(JiraTicket).order_by(JiraTicket.created_at).all()

    if not tickets:
        print(f"  {INFO} No jira_tickets rows found.\n")
        return

    matched = 0
    missing = 0
    errors  = 0

    for ticket in tickets:
        exists, summary_or_err = jira_issue_exists(ticket.jira_issue_key)

        if exists:
            matched += 1
            print(
                f"  {TICK} {ticket.jira_issue_key:<12} "
                f"DB title : '{ticket.title[:55]}'\n"
                f"             Jira title: '{summary_or_err}'\n"
                f"             Type: {ticket.issue_type} | Priority: {ticket.priority} "
                f"| From: {ticket.raised_from} | Status: {ticket.status}"
            )
        elif summary_or_err is None:
            missing += 1
            print(
                f"  {CROSS} {ticket.jira_issue_key:<12} NOT FOUND on Jira "
                f"(DB title: '{ticket.title[:55]}')"
            )
        else:
            errors += 1
            print(f"  {WARN} {ticket.jira_issue_key:<12} API error — {summary_or_err}")

        print()

    print(
        f"  Summary: {GREEN}{matched} matched{RESET}, "
        f"{RED}{missing} missing from Jira{RESET}, "
        f"{YELLOW}{errors} errors{RESET}\n"
    )


if __name__ == "__main__":
    db: Session = SessionLocal()
    try:
        verify(db)
    finally:
        db.close()
