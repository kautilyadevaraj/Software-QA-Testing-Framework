"""phase3 artifact registry.

Revision ID: 0015_phase3_artifacts
Revises: 0014_phase3_script_cache
Create Date: 2026-05-24
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0015_phase3_artifacts"
down_revision = "0014_phase3_script_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "phase3_artifacts",
        sa.Column("artifact_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("test_runs.run_id", ondelete="CASCADE"), nullable=False),
        sa.Column("test_id", UUID(as_uuid=True), sa.ForeignKey("test_cases.test_id", ondelete="CASCADE"), nullable=True),
        sa.Column("artifact_type", sa.VARCHAR(length=32), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.VARCHAR(length=128), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("status", sa.VARCHAR(length=16), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "artifact_type IN ('SCRIPT', 'TRACE', 'VIDEO', 'SCREENSHOT', 'XRAY_CSV', 'MANIFEST', 'REPORT')",
            name="ck_phase3_artifacts_type",
        ),
        sa.CheckConstraint("status IN ('ACTIVE', 'DELETED')", name="ck_phase3_artifacts_status"),
        sa.UniqueConstraint("run_id", "test_id", "artifact_type", "path", name="uq_phase3_artifact_identity"),
    )
    op.create_index("ix_phase3_artifacts_project_id", "phase3_artifacts", ["project_id"])
    op.create_index("ix_phase3_artifacts_run_id", "phase3_artifacts", ["run_id"])
    op.create_index("ix_phase3_artifacts_test_id", "phase3_artifacts", ["test_id"])
    op.create_index("ix_phase3_artifacts_artifact_type", "phase3_artifacts", ["artifact_type"])
    op.create_index("ix_phase3_artifacts_status", "phase3_artifacts", ["status"])


def downgrade() -> None:
    op.drop_index("ix_phase3_artifacts_status", table_name="phase3_artifacts")
    op.drop_index("ix_phase3_artifacts_artifact_type", table_name="phase3_artifacts")
    op.drop_index("ix_phase3_artifacts_test_id", table_name="phase3_artifacts")
    op.drop_index("ix_phase3_artifacts_run_id", table_name="phase3_artifacts")
    op.drop_index("ix_phase3_artifacts_project_id", table_name="phase3_artifacts")
    op.drop_table("phase3_artifacts")
