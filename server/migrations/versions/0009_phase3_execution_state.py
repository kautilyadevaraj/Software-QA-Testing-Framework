"""Phase 3 durable execution state

Revision ID: 0009_phase3_execution_state
Revises: 0008_phase3_scalable_foundation
Create Date: 2026-05-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects.postgresql import UUID

revision = "0009_phase3_execution_state"
down_revision = "0008_phase3_scalable_foundation"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "phase3_execution_state" not in _tables():
        op.create_table(
            "phase3_execution_state",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "run_id",
                UUID(as_uuid=True),
                sa.ForeignKey("test_runs.run_id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "test_id",
                UUID(as_uuid=True),
                sa.ForeignKey("test_cases.test_id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("status", sa.VARCHAR(32), nullable=False, server_default="PENDING"),
            sa.Column("retries", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("blocked_by", UUID(as_uuid=True), nullable=True),
            sa.Column("jira_ticket", sa.Text(), nullable=True),
            sa.Column("trace_path", sa.Text(), nullable=True),
            sa.Column("network_logs_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.UniqueConstraint("run_id", "test_id", name="uq_phase3_execution_state_run_test"),
        )
        op.create_index("ix_phase3_execution_state_run_id", "phase3_execution_state", ["run_id"])
        op.create_index("ix_phase3_execution_state_test_id", "phase3_execution_state", ["test_id"])


def downgrade() -> None:
    if "phase3_execution_state" in _tables():
        op.drop_table("phase3_execution_state")
