"""phase3_hls_groups — durable HLS group ordering, replaces state.json hls: keys.

Revision ID: 0013_phase3_hls_groups
Revises: 0012_phase3_job_claims
Create Date: 2026-05-04

Purpose:
    state_store.json stored `hls:{hls_id}` keys with ordered_test_ids so the
    worker could match JSON-reporter test() results to test_ids by position.
    In a multi-container worker setup the JSON file is unreachable from other
    containers, so this mapping must live in Postgres.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0013_phase3_hls_groups"
down_revision = "0012_phase3_job_claims"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "phase3_hls_groups",
        sa.Column("hls_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", UUID(as_uuid=True), nullable=False),
        sa.Column("ordered_test_ids", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_phase3_hls_groups_run_id", "phase3_hls_groups", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_phase3_hls_groups_run_id", table_name="phase3_hls_groups")
    op.drop_table("phase3_hls_groups")
