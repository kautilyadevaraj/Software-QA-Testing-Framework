"""Phase 3 run-scoped test results

Revision ID: 0010_run_scoped_test_results
Revises: 0009_phase3_execution_state
Create Date: 2026-05-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects.postgresql import UUID

revision = "0010_run_scoped_test_results"
down_revision = "0009_phase3_execution_state"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> dict[str, tuple[list[str], bool]]:
    return {
        idx["name"]: (list(idx.get("column_names") or []), bool(idx.get("unique")))
        for idx in inspect(op.get_bind()).get_indexes(table)
        if idx.get("name")
    }


def _unique_constraints(table: str) -> dict[str, list[str]]:
    return {
        c["name"]: list(c["column_names"] or [])
        for c in inspect(op.get_bind()).get_unique_constraints(table)
        if c.get("name")
    }


def _foreign_keys(table: str) -> dict[str, tuple[list[str], str, list[str]]]:
    return {
        fk["name"]: (
            list(fk.get("constrained_columns") or []),
            fk.get("referred_table") or "",
            list(fk.get("referred_columns") or []),
        )
        for fk in inspect(op.get_bind()).get_foreign_keys(table)
        if fk.get("name")
    }


def _drop_fk_by_shape(table: str, columns: list[str], referred_table: str, referred_columns: list[str]) -> None:
    for name, shape in _foreign_keys(table).items():
        if shape == (columns, referred_table, referred_columns):
            op.drop_constraint(name, table, type_="foreignkey")


def upgrade() -> None:
    tr_uniques = _unique_constraints("test_results")
    for name, columns in tr_uniques.items():
        if columns == ["test_id"]:
            op.drop_constraint(name, "test_results", type_="unique")
    for name, (columns, is_unique) in _indexes("test_results").items():
        if columns == ["test_id"] and is_unique:
            op.drop_index(name, table_name="test_results")

    tr_uniques = _unique_constraints("test_results")
    if "uq_test_results_run_test" not in tr_uniques:
        op.create_unique_constraint(
            "uq_test_results_run_test",
            "test_results",
            ["run_id", "test_id"],
        )

    for table in ("network_logs", "retry_history"):
        cols = _columns(table)
        if "test_result_id" not in cols:
            op.add_column(table, sa.Column("test_result_id", UUID(as_uuid=True), nullable=True))
            op.create_index(f"ix_{table}_test_result_id", table, ["test_result_id"])

        _drop_fk_by_shape(table, ["test_id"], "test_results", ["test_id"])

        fks = _foreign_keys(table)
        if f"fk_{table}_test_result_id_test_results" not in fks:
            op.create_foreign_key(
                f"fk_{table}_test_result_id_test_results",
                table,
                "test_results",
                ["test_result_id"],
                ["id"],
                ondelete="CASCADE",
            )

        fks = _foreign_keys(table)
        if f"fk_{table}_test_id_test_cases" not in fks:
            op.create_foreign_key(
                f"fk_{table}_test_id_test_cases",
                table,
                "test_cases",
                ["test_id"],
                ["test_id"],
                ondelete="CASCADE",
            )

    op.execute(
        """
        UPDATE network_logs nl
        SET test_result_id = tr.id
        FROM test_results tr
        WHERE nl.test_result_id IS NULL
          AND nl.test_id = tr.test_id
        """
    )
    op.execute(
        """
        UPDATE retry_history rh
        SET test_result_id = tr.id
        FROM test_results tr
        WHERE rh.test_result_id IS NULL
          AND rh.test_id = tr.test_id
        """
    )


def downgrade() -> None:
    for table in ("network_logs", "retry_history"):
        _drop_fk_by_shape(table, ["test_id"], "test_cases", ["test_id"])
        _drop_fk_by_shape(table, ["test_result_id"], "test_results", ["id"])
        if f"ix_{table}_test_result_id" in _indexes(table):
            op.drop_index(f"ix_{table}_test_result_id", table_name=table)
        if "test_result_id" in _columns(table):
            op.drop_column(table, "test_result_id")

    tr_uniques = _unique_constraints("test_results")
    if "uq_test_results_run_test" in tr_uniques:
        op.drop_constraint("uq_test_results_run_test", "test_results", type_="unique")
    tr_uniques = _unique_constraints("test_results")
    if not any(columns == ["test_id"] for columns in tr_uniques.values()):
        op.create_unique_constraint("uq_test_results_test_id", "test_results", ["test_id"])

    for table in ("network_logs", "retry_history"):
        fks = _foreign_keys(table)
        if f"fk_{table}_test_id_test_results" not in fks:
            op.create_foreign_key(
                f"fk_{table}_test_id_test_results",
                table,
                "test_results",
                ["test_id"],
                ["test_id"],
                ondelete="CASCADE",
            )
