"""Jira Cloud REST API v3 integration service.

All Jira API calls are centralised here. The rest of the codebase never
imports ``requests`` or builds Jira URLs directly.

Authentication: HTTP Basic Auth using (email, API token).
Docs: https://developer.atlassian.com/cloud/jira/platform/rest/v3/
"""

from __future__ import annotations

import re
from typing import TypedDict

import requests
from requests.auth import HTTPBasicAuth

from app.core.config import get_settings


# ---------------------------------------------------------------------------
# Response type hints
# ---------------------------------------------------------------------------


class JiraProjectResult(TypedDict):
    jira_project_key: str
    jira_project_id: str


class JiraIssueResult(TypedDict):
    jira_issue_key: str   # e.g. "MSA-1"
    jira_issue_id: str    # Jira internal numeric/string id


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _auth() -> HTTPBasicAuth:
    settings = get_settings()
    return HTTPBasicAuth(settings.jira_email or "", settings.jira_api_token or "")


def _base_url() -> str:
    settings = get_settings()
    return (settings.jira_base_url or "").rstrip("/")


def _headers() -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _raise_for_jira(response: requests.Response) -> None:
    """Raise a RuntimeError with the Jira error message on non-2xx responses."""
    if not response.ok:
        try:
            detail = response.json()
            messages = detail.get("errorMessages", [])
            errors = detail.get("errors", {})
            msg = "; ".join(messages) or "; ".join(f"{k}: {v}" for k, v in errors.items())
        except Exception:
            msg = response.text or f"HTTP {response.status_code}"
        raise RuntimeError(f"Jira API error ({response.status_code}): {msg}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def is_jira_configured() -> bool:
    """Return True only when all four Jira env vars are set and non-empty."""
    s = get_settings()
    return bool(s.jira_base_url and s.jira_email and s.jira_api_token and s.jira_lead_account_id)


def generate_jira_key(project_name: str) -> str:
    """Derive a valid Jira project key from a human-readable project name.

    Rules enforced by Jira:
    - Uppercase letters only (A-Z)
    - 2–10 characters
    - Must start with a letter

    Strategy: take the first letter of each word in the name, uppercase,
    strip non-alpha chars, truncate to 10. Fall back to "SQAT" if the
    result is empty.

    Examples:
        "My Shopping App"   → "MSA"
        "E-commerce Platform" → "EP"
        "Auth"              → "AUTH"
        "123 Numbers"       → "N"  (digits stripped, only "Numbers" contributes)
    """
    words = re.split(r"[\s\-_/]+", project_name.strip())
    key = "".join(w[0] for w in words if w and w[0].isalpha()).upper()[:10]
    return key if len(key) >= 2 else (re.sub(r"[^A-Z]", "", project_name.upper())[:10] or "SQAT")


def create_jira_project(
    name: str,
    key: str,
    description: str = "",
) -> JiraProjectResult:
    """Create a new Jira project and return its key and internal ID.

    Requires the Jira account to have *Create project* permission
    (typically site-admin or project-admin).

    Args:
        name:        Human-readable project name (displayed in Jira).
        key:         Jira project key (e.g. ``"MSA"``). Must be unique workspace-wide.
        description: Optional description shown in Jira.

    Returns:
        A dict with ``jira_project_key`` and ``jira_project_id``.

    Raises:
        RuntimeError: if Jira credentials are not configured or the API call fails.
    """
    if not is_jira_configured():
        raise RuntimeError(
            "Jira credentials are not configured. "
            "Set JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN, and JIRA_LEAD_ACCOUNT_ID in .env."
        )

    url = f"{_base_url()}/rest/api/3/project"

    # Defensively strip any ?cloudId=... or similar suffix the Jira console
    # sometimes appends when users copy the account ID from the UI.
    lead_account_id = (get_settings().jira_lead_account_id or "").split("?")[0].strip()

    payload: dict = {
        "name": name,
        "key": key,
        "description": description,
        # Required: the Jira account that will be the project lead
        "leadAccountId": lead_account_id,
        # Software project with a Scrum board — most common for QA teams.
        # Change to "business" for business projects if needed.
        "projectTypeKey": "software",
        "projectTemplateKey": "com.pyxis.greenhopper.jira:gh-scrum-template",
        "assigneeType": "UNASSIGNED",
    }

    response = requests.post(url, json=payload, headers=_headers(), auth=_auth(), timeout=15)
    _raise_for_jira(response)

    data: dict = response.json()
    return JiraProjectResult(
        jira_project_key=data["key"],
        jira_project_id=str(data["id"]),
    )


def create_jira_issue(
    project_key: str,
    title: str,
    description: str,
    issue_type: str = "Bug",
    priority: str = "Medium",
) -> JiraIssueResult:
    """Create a Jira issue (ticket) in the specified project.

    Args:
        project_key: Jira project key, e.g. ``"MSA"``.
        title:       Issue summary / title.
        description: Plain-text description (converted to Atlassian Document Format).
        issue_type:  One of ``"Bug"``, ``"Task"``, ``"Story"``.
        priority:    One of ``"High"``, ``"Medium"``, ``"Low"``.

    Returns:
        A dict with ``jira_issue_key`` (e.g. ``"MSA-1"``) and ``jira_issue_id``.

    Raises:
        RuntimeError: if Jira credentials are not configured or the API call fails.
    """
    if not is_jira_configured():
        raise RuntimeError(
            "Jira credentials are not configured. "
            "Set JIRA_BASE_URL, JIRA_EMAIL, and JIRA_API_TOKEN in .env."
        )

    url = f"{_base_url()}/rest/api/3/issue"

    # Jira API v3 requires description in Atlassian Document Format (ADF)
    adf_description = {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": description or " "}],
            }
        ],
    }

    payload: dict = {
        "fields": {
            "project": {"key": project_key},
            "summary": title,
            "description": adf_description,
            "issuetype": {"name": issue_type},
            "priority": {"name": priority},
        }
    }

    response = requests.post(url, json=payload, headers=_headers(), auth=_auth(), timeout=15)
    _raise_for_jira(response)

    data: dict = response.json()
    return JiraIssueResult(
        jira_issue_key=data["key"],
        jira_issue_id=str(data["id"]),
    )
