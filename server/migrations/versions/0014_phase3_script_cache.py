"""phase3 script cache metadata.

Revision ID: 0014_phase3_script_cache
Revises: 0013_phase3_hls_groups
Create Date: 2026-05-24
"""
from alembic import op
import sqlalchemy as sa

revision = "0014_phase3_script_cache"
down_revision = "0013_phase3_hls_groups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("test_cases", sa.Column("content_hash", sa.VARCHAR(length=64), nullable=True))
    op.add_column("test_cases", sa.Column("context_hash", sa.VARCHAR(length=64), nullable=True))
    op.add_column("test_cases", sa.Column("script_generator_version", sa.VARCHAR(length=64), nullable=True))
    op.add_column("test_cases", sa.Column("script_status", sa.VARCHAR(length=32), nullable=True))
    op.add_column("test_cases", sa.Column("script_error", sa.Text(), nullable=True))
    op.create_index("ix_test_cases_content_hash", "test_cases", ["content_hash"])
    op.create_index("ix_test_cases_context_hash", "test_cases", ["context_hash"])
    op.create_index("ix_test_cases_script_status", "test_cases", ["script_status"])


def downgrade() -> None:
    op.drop_index("ix_test_cases_script_status", table_name="test_cases")
    op.drop_index("ix_test_cases_context_hash", table_name="test_cases")
    op.drop_index("ix_test_cases_content_hash", table_name="test_cases")
    op.drop_column("test_cases", "script_error")
    op.drop_column("test_cases", "script_status")
    op.drop_column("test_cases", "script_generator_version")
    op.drop_column("test_cases", "context_hash")
    op.drop_column("test_cases", "content_hash")
