"""phase3 assertion evidence.

Revision ID: 0016_phase3_assertion_evidence
Revises: 0015_phase3_artifacts
Create Date: 2026-05-25
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "0016_phase3_assertion_evidence"
down_revision = "0015_phase3_artifacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "test_cases",
        sa.Column(
            "assertion_evidence",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.alter_column("test_cases", "assertion_evidence", server_default=None)


def downgrade() -> None:
    op.drop_column("test_cases", "assertion_evidence")
