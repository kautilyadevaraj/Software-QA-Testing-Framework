"""Phase 3: test_cases, test_results, network_logs, retry_history, test_runs, review_queue

Revision ID: 0006_phase3_tables
Revises: 0005_launch_scenario_id
Create Date: 2026-04-27
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY

revision = "0006_phase3_tables"
down_revision = "0005_launch_scenario_id"
branch_labels = None
depends_on = None


def _existing_tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    existing = _existing_tables()

    # ── test_cases ─────────────────────────────────────────────────────────
    if "test_cases" not in existing:
        op.create_table(
            "test_cases",
            sa.Column("test_id", UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "project_id",
                UUID(as_uuid=True),
                sa.ForeignKey("projects.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("title", sa.Text, nullable=False),
            sa.Column("steps", JSONB, nullable=False, server_default="[]"),
            sa.Column("depends_on", ARRAY(UUID(as_uuid=True)), nullable=False, server_default="{}"),
            sa.Column("target_page", sa.Text, nullable=False),
            sa.Column("script_path", sa.Text, nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )

    # ── test_runs ──────────────────────────────────────────────────────────
    if "test_runs" not in existing:
        op.create_table(
            "test_runs",
            sa.Column("run_id", UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "project_id",
                UUID(as_uuid=True),
                sa.ForeignKey("projects.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("total", sa.Integer, nullable=False, server_default="0"),
            sa.Column("passed", sa.Integer, nullable=False, server_default="0"),
            sa.Column("failed", sa.Integer, nullable=False, server_default="0"),
            sa.Column("skipped", sa.Integer, nullable=False, server_default="0"),
            sa.Column("human_review", sa.Integer, nullable=False, server_default="0"),
            sa.Column("duration_seconds", sa.Integer, nullable=True),
            sa.Column("status", sa.VARCHAR(32), nullable=False, server_default="running"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )

    # ── test_results ───────────────────────────────────────────────────────
    if "test_results" not in existing:
        op.create_table(
            "test_results",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "test_id",
                UUID(as_uuid=True),
                sa.ForeignKey("test_cases.test_id", ondelete="CASCADE"),
                nullable=False,
                unique=True,
                index=True,
            ),
            sa.Column("status", sa.VARCHAR(32), nullable=False),
            sa.Column("retries", sa.Integer, nullable=False, server_default="0"),
            sa.Column("jira_ticket", sa.Text, nullable=True),
            sa.Column("trace_path", sa.Text, nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )

    # ── network_logs ───────────────────────────────────────────────────────
    if "network_logs" not in existing:
        op.create_table(
            "network_logs",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "test_id",
                UUID(as_uuid=True),
                sa.ForeignKey("test_results.test_id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("url", sa.Text, nullable=False),
            sa.Column("method", sa.VARCHAR(16), nullable=False),
            sa.Column("status_code", sa.Integer, nullable=False),
            sa.Column("is_failure", sa.Boolean, nullable=False, server_default="false"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )

    # ── retry_history ──────────────────────────────────────────────────────
    if "retry_history" not in existing:
        op.create_table(
            "retry_history",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "test_id",
                UUID(as_uuid=True),
                sa.ForeignKey("test_results.test_id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("attempt_number", sa.Integer, nullable=False),
            sa.Column("error_snapshot", sa.Text, nullable=False),
            sa.Column("llm_fix_applied", sa.Text, nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )

    # ── review_queue ───────────────────────────────────────────────────────
    if "review_queue" not in existing:
        op.create_table(
            "review_queue",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "test_id",
                UUID(as_uuid=True),
                sa.ForeignKey("test_cases.test_id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column(
                "run_id",
                UUID(as_uuid=True),
                sa.ForeignKey("test_runs.run_id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column(
                "review_type",
                sa.VARCHAR(16),
                sa.CheckConstraint("review_type IN ('BUG', 'TASK')", name="ck_review_queue_type"),
                nullable=False,
            ),
            sa.Column("evidence", JSONB, nullable=False, server_default="{}"),
            sa.Column("status", sa.VARCHAR(32), nullable=False, server_default="pending"),
            sa.Column("jira_ref", sa.Text, nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )


def downgrade() -> None:
    op.drop_table("review_queue")
    op.drop_table("retry_history")
    op.drop_table("network_logs")
    op.drop_table("test_results")
    op.drop_table("test_runs")
    op.drop_table("test_cases")
