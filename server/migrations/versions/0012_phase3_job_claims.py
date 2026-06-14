"""phase3_job_claims table — worker idempotency / poison-pill dedup.

Revision ID: 0012_phase3_job_claims
Revises: 0011_drop_result_unique_idx
Create Date: 2026-05-04

Purpose:
    When RabbitMQ redelivers a message after a worker crash (at-least-once
    semantics), a second worker could run the same Playwright test again and
    raise duplicate Jira bugs / write duplicate rows. This table acts as a
    per-job_id idempotency lock backed by a UNIQUE constraint on job_id.
"""
from alembic import op
import sqlalchemy as sa

revision = "0012_phase3_job_claims"
down_revision = "0011_drop_result_unique_idx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "phase3_job_claims",
        sa.Column("job_id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="claimed"),
        sa.Column("worker_host", sa.String(length=128), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "claimed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_phase3_job_claims_run_id",
        "phase3_job_claims",
        ["run_id"],
    )
    op.create_index(
        "ix_phase3_job_claims_claimed_at",
        "phase3_job_claims",
        ["claimed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_phase3_job_claims_claimed_at", table_name="phase3_job_claims")
    op.drop_index("ix_phase3_job_claims_run_id", table_name="phase3_job_claims")
    op.drop_table("phase3_job_claims")
