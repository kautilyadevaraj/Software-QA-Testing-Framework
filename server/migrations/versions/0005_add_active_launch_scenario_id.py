"""Add active_launch_scenario_id to projects

Revision ID: 0005_launch_scenario_id
Revises: 0004_phase2_ui_discovery
Create Date: 2026-04-26
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0005_launch_scenario_id"
down_revision = "0004_phase2_ui_discovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotent: column may already exist if create_all() ran before this migration
    op.execute(
        """
        ALTER TABLE projects
        ADD COLUMN IF NOT EXISTS active_launch_scenario_id UUID NULL;
        """
    )


def downgrade() -> None:
    op.drop_column("projects", "active_launch_scenario_id")
