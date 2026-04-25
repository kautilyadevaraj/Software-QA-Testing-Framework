"""cleanup.py — Dev utility: wipe all projects, Jira tickets, and Jira projects.

⚠  DESTRUCTIVE — This script permanently deletes data. Use only in development.

What it does, in order:
  1. Deletes every Jira issue (ticket) from the Jira board via the API
  2. Deletes every Jira project from the Jira board via the API
  3. Deletes all rows from jira_tickets (local DB)
  4. Deletes all rows from project_jira_config (local DB)
  5. Deletes all projects from the local DB (cascades to files, members, etc.)

Usage (from server/ with venv activated):
    python cleanup.py                  ← shows a confirmation prompt first
    python cleanup.py --confirm        ← skips the prompt (CI/scripts)
    python cleanup.py --db-only        ← skips Jira API calls, cleans DB only
    python cleanup.py --jira-only      ← cleans Jira only, leaves DB untouched
"""

from __future__ import annotations

import sys
import os
import argparse
import time

# ── Make sure app imports work when run from server/ directly ────────────────
sys.path.insert(0, os.path.dirname(__file__))

import requests
from requests.auth import HTTPBasicAuth
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.project import (
    JiraTicket,
    ProjectJiraConfig,
    Project,
    ProjectFile,
    ProjectMember,
)
from app.models.user import User  # noqa: F401

# ── Terminal colours ─────────────────────────────────────────────────────────
RED    = "\033[91m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

TICK  = f"{GREEN}✔{RESET}"
CROSS = f"{RED}✘{RESET}"
WARN  = f"{YELLOW}⚠{RESET}"
INFO  = f"{CYAN}ℹ{RESET}"


# ── Jira helpers ─────────────────────────────────────────────────────────────

def _auth() -> HTTPBasicAuth:
    s = get_settings()
    return HTTPBasicAuth(s.jira_email or "", s.jira_api_token or "")


def _base() -> str:
    return (get_settings().jira_base_url or "").rstrip("/")


def _headers() -> dict[str, str]:
    return {"Accept": "application/json", "Content-Type": "application/json"}


def delete_jira_issue(issue_key: str) -> tuple[bool, str]:
    """Delete a single Jira issue. Returns (success, message)."""
    url = f"{_base()}/rest/api/3/issue/{issue_key}"
    try:
        resp = requests.delete(url, auth=_auth(), headers=_headers(), timeout=10)
        if resp.status_code == 204:
            return True, "deleted"
        if resp.status_code == 404:
            return True, "already gone (404)"
        return False, f"HTTP {resp.status_code}: {resp.text[:120]}"
    except requests.RequestException as exc:
        return False, f"Network error: {exc}"


def delete_jira_project(project_key: str) -> tuple[bool, str]:
    """Delete a Jira project and ALL its issues. Returns (success, message).

    Note: Jira deletes all issues inside the project automatically.
    """
    url = f"{_base()}/rest/api/3/project/{project_key}"
    try:
        resp = requests.delete(url, auth=_auth(), headers=_headers(), timeout=15)
        if resp.status_code == 204:
            return True, "deleted"
        if resp.status_code == 404:
            return True, "already gone (404)"
        return False, f"HTTP {resp.status_code}: {resp.text[:120]}"
    except requests.RequestException as exc:
        return False, f"Network error: {exc}"


# ── Cleanup sections ─────────────────────────────────────────────────────────

def cleanup_jira_tickets(db: Session) -> None:
    """Delete every local jira_ticket from Jira, then from DB."""
    print(f"\n{BOLD}── Step 1: Delete Jira Issues ────────────────────────────{RESET}")
    tickets = db.query(JiraTicket).all()

    if not tickets:
        print(f"  {INFO} No jira_tickets rows found — nothing to delete.")
        return

    print(f"  Found {len(tickets)} ticket(s) in local DB.\n")
    ok = 0
    fail = 0

    for ticket in tickets:
        success, msg = delete_jira_issue(ticket.jira_issue_key)
        if success:
            ok += 1
            print(f"  {TICK} {ticket.jira_issue_key:<12} {msg}")
        else:
            fail += 1
            print(f"  {CROSS} {ticket.jira_issue_key:<12} {msg}")
        # Respect Jira rate limits (10 req/s for Cloud free tier)
        time.sleep(0.15)

    print(f"\n  Jira: {ok} deleted, {fail} failed.")


def cleanup_jira_projects(db: Session) -> None:
    """Delete every linked Jira project (which removes all its issues on Jira)."""
    print(f"\n{BOLD}── Step 2: Delete Jira Projects ──────────────────────────{RESET}")
    configs = db.query(ProjectJiraConfig).all()

    if not configs:
        print(f"  {INFO} No project_jira_config rows found — nothing to delete.")
        return

    print(f"  Found {len(configs)} Jira project(s) linked.\n")
    ok = 0
    fail = 0

    for cfg in configs:
        success, msg = delete_jira_project(cfg.jira_project_key)
        if success:
            ok += 1
            print(f"  {TICK} [{cfg.jira_project_key}] {msg}")
        else:
            fail += 1
            print(f"  {CROSS} [{cfg.jira_project_key}] {msg}")
        time.sleep(0.3)

    print(f"\n  Jira: {ok} deleted, {fail} failed.")


def cleanup_db(db: Session) -> None:
    """Wipe jira_tickets, project_jira_config, and all projects from the DB."""
    print(f"\n{BOLD}── Step 3: Clean Local Database ──────────────────────────{RESET}")

    # Tickets
    ticket_count = db.query(JiraTicket).count()
    db.query(JiraTicket).delete(synchronize_session=False)
    print(f"  {TICK} Deleted {ticket_count} row(s) from jira_tickets")

    # Jira configs
    config_count = db.query(ProjectJiraConfig).count()
    db.query(ProjectJiraConfig).delete(synchronize_session=False)
    print(f"  {TICK} Deleted {config_count} row(s) from project_jira_config")

    # Projects (cascades to project_members, project_files,
    # extracted_text, api_endpoints, project_credential_verification)
    project_count = db.query(Project).count()
    db.query(Project).delete(synchronize_session=False)
    print(f"  {TICK} Deleted {project_count} project(s) (cascade: members, files, etc.)")

    db.commit()
    print(f"\n  Database is clean.")


# ── Entry point ───────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SQAT dev cleanup utility")
    p.add_argument(
        "--confirm",
        action="store_true",
        help="Skip the interactive confirmation prompt",
    )
    p.add_argument(
        "--db-only",
        action="store_true",
        help="Only clean the local database, skip Jira API calls",
    )
    p.add_argument(
        "--jira-only",
        action="store_true",
        help="Only delete from Jira, leave local DB untouched",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    settings = get_settings()

    print(f"\n{BOLD}{RED}═══ SQAT Cleanup Utility ═══{RESET}{BOLD} ⚠  DESTRUCTIVE{RESET}\n")

    # ── Mode summary ─────────────────────────────────────────────────────────
    if args.db_only:
        print(f"  Mode    : {YELLOW}DB only{RESET} (Jira API calls skipped)")
    elif args.jira_only:
        print(f"  Mode    : {YELLOW}Jira only{RESET} (local DB untouched)")
    else:
        print(f"  Mode    : {RED}Full cleanup{RESET} (Jira + DB)")

    print(f"  Jira    : {settings.jira_base_url or 'NOT CONFIGURED'}")
    print(f"  Targets : all projects, all jira tickets, all jira projects\n")

    # ── Confirmation ─────────────────────────────────────────────────────────
    if not args.confirm:
        answer = input(
            f"{RED}This will permanently delete data. Type 'yes' to continue: {RESET}"
        ).strip().lower()
        if answer != "yes":
            print(f"\n{WARN} Aborted — nothing was deleted.\n")
            return

    jira_configured = all([
        settings.jira_base_url,
        settings.jira_email,
        settings.jira_api_token,
    ])

    db: Session = SessionLocal()
    try:
        if not args.db_only:
            if not jira_configured:
                print(f"\n{WARN} Jira credentials not configured — skipping Jira API steps.")
            else:
                # Step 1: delete individual tickets from Jira
                # (Step 2 would delete them anyway via project deletion,
                #  but we do it explicitly so the per-ticket log is clear)
                cleanup_jira_tickets(db)

                # Step 2: delete Jira projects (also removes any remaining issues)
                cleanup_jira_projects(db)

        if not args.jira_only:
            # Step 3: wipe local DB
            cleanup_db(db)

    finally:
        db.close()

    print(f"\n{GREEN}{BOLD}Done.{RESET}\n")


if __name__ == "__main__":
    main()
