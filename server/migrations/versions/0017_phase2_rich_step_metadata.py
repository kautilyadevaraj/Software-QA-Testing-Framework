"""Phase 2 rich recorder step metadata.

Revision ID: 0017_phase2_rich_step_metadata
Revises: 0016_phase3_assertion_evidence
Create Date: 2026-05-28
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "0017_phase2_rich_step_metadata"
down_revision = "0016_phase3_assertion_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_scenario_steps_action_type", "scenario_steps", type_="check")
    op.create_check_constraint(
        "ck_scenario_steps_action_type",
        "scenario_steps",
        "action_type IN ('navigate','click','fill','select','hover','keypress','scroll','check','uncheck','slide','submit')",
    )

    op.add_column("scenario_steps", sa.Column("selector_stability", sa.Text(), nullable=True))
    op.add_column("scenario_steps", sa.Column("playwright_locator", sa.Text(), nullable=True))
    op.add_column("scenario_steps", sa.Column("accessible_name", sa.Text(), nullable=True))
    op.add_column("scenario_steps", sa.Column("role", sa.Text(), nullable=True))
    op.add_column("scenario_steps", sa.Column("label", sa.Text(), nullable=True))
    op.add_column("scenario_steps", sa.Column("input_type", sa.Text(), nullable=True))
    op.add_column("scenario_steps", sa.Column("url_before", sa.Text(), nullable=True))
    op.add_column("scenario_steps", sa.Column("url_after", sa.Text(), nullable=True))
    op.add_column("scenario_steps", sa.Column("caused_navigation", sa.Boolean(), nullable=True))
    op.add_column("scenario_steps", sa.Column("semantic_context", JSONB(), nullable=True))

    op.execute(
        """
        WITH ranked AS (
            SELECT
                id,
                row_number() OVER (
                    PARTITION BY recording_session_id, step_index
                    ORDER BY created_at, id
                ) AS duplicate_rank
            FROM scenario_steps
        )
        UPDATE scenario_steps AS s
        SET step_index = s.step_index + (ranked.duplicate_rank * 1000000)
        FROM ranked
        WHERE s.id = ranked.id
          AND ranked.duplicate_rank > 1
        """
    )
    op.create_index(
        "ux_scenario_steps_recording_session_step_index",
        "scenario_steps",
        ["recording_session_id", "step_index"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ux_scenario_steps_recording_session_step_index",
        table_name="scenario_steps",
    )
    op.drop_column("scenario_steps", "semantic_context")
    op.drop_column("scenario_steps", "caused_navigation")
    op.drop_column("scenario_steps", "url_after")
    op.drop_column("scenario_steps", "url_before")
    op.drop_column("scenario_steps", "input_type")
    op.drop_column("scenario_steps", "label")
    op.drop_column("scenario_steps", "role")
    op.drop_column("scenario_steps", "accessible_name")
    op.drop_column("scenario_steps", "playwright_locator")
    op.drop_column("scenario_steps", "selector_stability")
    op.drop_constraint("ck_scenario_steps_action_type", "scenario_steps", type_="check")
    op.create_check_constraint(
        "ck_scenario_steps_action_type",
        "scenario_steps",
        "action_type IN ('navigate','click','fill','select','hover','keypress','scroll')",
    )
