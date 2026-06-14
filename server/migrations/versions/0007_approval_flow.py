"""Phase 3 Approval Flow: tc_number, acceptance_criteria, hls_id, approval_status, run_type

Revision ID: 0007_approval_flow
Revises: 0006_phase3_tables
Create Date: 2026-04-30

Adds:
  test_cases.tc_number           — human-readable number, e.g. TC-001 (RTM traceability)
  test_cases.acceptance_criteria — JSONB list of verifiable pass conditions
  test_cases.hls_id              — soft-FK to high_level_scenarios (UI grouping)
  test_cases.approval_status     — PENDING | APPROVED | NEEDS_EDIT (approval gate)
  test_runs.run_type             — 'plan' | 'execute' (distinguishes planning from execution)

Design notes:
  - hls_id is stored as plain UUID (no enforced FK constraint) so that deleting a
    scenario never cascades and removes test cases. The ORM relationship is advisory only.
  - acceptance_criteria server_default is the SQL literal '[]' (quoted for JSONB).
  - All columns are additive (nullable or have server_default) — zero downtime.
  - downgrade uses if_exists=True on drop_index to survive partial rollbacks.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "0007_approval_flow"
down_revision = "0006_phase3_tables"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    """Return the set of existing column names for a table (using current connection)."""
    conn = op.get_bind()
    return {c["name"] for c in inspect(conn).get_columns(table)}


def _indexes(table: str) -> set[str]:
    """Return the set of existing index names for a table."""
    conn = op.get_bind()
    return {idx["name"] for idx in inspect(conn).get_indexes(table)}


def upgrade() -> None:
    tc_cols = _columns("test_cases")
    tr_cols  = _columns("test_runs")

    # ── test_cases ────────────────────────────────────────────────────────────

    if "tc_number" not in tc_cols:
        op.add_column(
            "test_cases",
            sa.Column("tc_number", sa.VARCHAR(20), nullable=True),
        )

    if "acceptance_criteria" not in tc_cols:
        op.add_column(
            "test_cases",
            sa.Column(
                "acceptance_criteria",
                JSONB,
                nullable=False,
                # JSONB server_default must be a SQL-quoted literal: '[]'
                server_default=text("'[]'"),
            ),
        )

    if "hls_id" not in tc_cols:
        # Stored as plain UUID — no enforced FK constraint.
        # Rationale: we never want deleting a Phase 2 scenario to cascade-delete
        # approved test cases. The ORM relationship is advisory only.
        op.add_column(
            "test_cases",
            sa.Column("hls_id", UUID(as_uuid=True), nullable=True),
        )

    if "approval_status" not in tc_cols:
        op.add_column(
            "test_cases",
            sa.Column(
                "approval_status",
                sa.VARCHAR(20),
                nullable=False,
                server_default="PENDING",
            ),
        )

    # ── Indexes ───────────────────────────────────────────────────────────────
    existing_idx = _indexes("test_cases")

    if "idx_tc_hls_id" not in existing_idx:
        op.create_index("idx_tc_hls_id", "test_cases", ["hls_id"])

    if "idx_tc_number" not in existing_idx:
        op.create_index("idx_tc_number", "test_cases", ["tc_number"])

    if "idx_tc_approval_status" not in existing_idx:
        op.create_index("idx_tc_approval_status", "test_cases", ["approval_status"])

    # ── test_runs ─────────────────────────────────────────────────────────────

    if "run_type" not in tr_cols:
        op.add_column(
            "test_runs",
            sa.Column(
                "run_type",
                sa.VARCHAR(20),
                nullable=False,
                server_default="execute",
            ),
        )


def downgrade() -> None:
    # if_exists=True prevents crash when index was never created (partial migration)
    op.drop_index("idx_tc_approval_status", table_name="test_cases", if_exists=True)
    op.drop_index("idx_tc_number",          table_name="test_cases", if_exists=True)
    op.drop_index("idx_tc_hls_id",          table_name="test_cases", if_exists=True)

    op.drop_column("test_cases", "approval_status")
    op.drop_column("test_cases", "hls_id")
    op.drop_column("test_cases", "acceptance_criteria")
    op.drop_column("test_cases", "tc_number")

    op.drop_column("test_runs", "run_type")
