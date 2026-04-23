"""Add project_jira_config and jira_tickets tables

Revision ID: 0002_add_jira_tables
Revises: 0001_consolidated
Create Date: 2026-04-24

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_add_jira_tables"
down_revision: Union[str, None] = "0001_consolidated"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── project_jira_config ────────────────────────────────────────────────────
    # One row per project — UNIQUE constraint on project_id ensures no duplicates.
    op.create_table(
        "project_jira_config",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("jira_project_key", sa.String(20), nullable=False),
        sa.Column("jira_project_id", sa.String(50), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # DB-level guard: one app project → one Jira project, forever
        sa.UniqueConstraint("project_id", name="uq_project_jira_config_project_id"),
    )
    op.create_index(
        "ix_project_jira_config_project_id",
        "project_jira_config",
        ["project_id"],
    )

    # ── jira_tickets ───────────────────────────────────────────────────────────
    # Local record of every ticket raised from within this app.
    op.create_table(
        "jira_tickets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("jira_issue_key", sa.String(50), nullable=False),
        sa.Column("jira_issue_id", sa.String(50), nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("issue_type", sa.String(50), nullable=False, server_default="Bug"),
        sa.Column("priority", sa.String(20), nullable=False, server_default="Medium"),
        sa.Column("status", sa.String(50), nullable=False, server_default="Open"),
        sa.Column("raised_from", sa.String(50), nullable=False, server_default="url_section"),
        sa.Column(
            "raised_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_jira_tickets_project_id",
        "jira_tickets",
        ["project_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_jira_tickets_project_id", table_name="jira_tickets")
    op.drop_table("jira_tickets")
    op.drop_index("ix_project_jira_config_project_id", table_name="project_jira_config")
    op.drop_table("project_jira_config")
