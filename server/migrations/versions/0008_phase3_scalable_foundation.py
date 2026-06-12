"""Phase 3 scalable foundation: credential profiles, auth states, run-scoped TCs

Revision ID: 0008_phase3_scalable_foundation
Revises: 0007_approval_flow
Create Date: 2026-05-02
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text
from sqlalchemy.dialects.postgresql import UUID

revision = "0008_phase3_scalable_foundation"
down_revision = "0007_approval_flow"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {idx["name"] for idx in inspect(op.get_bind()).get_indexes(table)}


def _constraints(table: str) -> set[str]:
    inspector = inspect(op.get_bind())
    names = {c["name"] for c in inspector.get_unique_constraints(table)}
    names.update(c["name"] for c in inspector.get_check_constraints(table))
    names.update(c["name"] for c in inspector.get_foreign_keys(table))
    return {n for n in names if n}


def upgrade() -> None:
    tables = _tables()

    if "credential_profiles" not in tables:
        op.create_table(
            "credential_profiles",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "project_id",
                UUID(as_uuid=True),
                sa.ForeignKey("projects.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "source_file_id",
                UUID(as_uuid=True),
                sa.ForeignKey("project_files.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("username", sa.String(255), nullable=False),
            sa.Column("password_ciphertext", sa.Text(), nullable=False, server_default=""),
            sa.Column("role", sa.String(100), nullable=False, server_default="user"),
            sa.Column("auth_type", sa.String(100), nullable=False, server_default=""),
            sa.Column("endpoint", sa.String(2048), nullable=False, server_default=""),
            sa.Column("is_verified", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.UniqueConstraint(
                "project_id",
                "username",
                "role",
                name="uq_credential_profiles_project_username_role",
            ),
        )
        op.create_index("ix_credential_profiles_project_id", "credential_profiles", ["project_id"])
        op.create_index("ix_credential_profiles_source_file_id", "credential_profiles", ["source_file_id"])

    tc_cols = _columns("test_cases")
    if "run_id" not in tc_cols:
        op.add_column("test_cases", sa.Column("run_id", UUID(as_uuid=True), nullable=True))
        op.create_foreign_key(
            "fk_test_cases_run_id_test_runs",
            "test_cases",
            "test_runs",
            ["run_id"],
            ["run_id"],
            ondelete="SET NULL",
        )
        op.create_index("ix_test_cases_run_id", "test_cases", ["run_id"])

    if "auth_mode" not in tc_cols:
        op.add_column(
            "test_cases",
            sa.Column(
                "auth_mode",
                sa.VARCHAR(32),
                nullable=False,
                server_default="authenticated",
            ),
        )
        op.create_index("ix_test_cases_auth_mode", "test_cases", ["auth_mode"])

    if "credential_id" not in tc_cols:
        op.add_column("test_cases", sa.Column("credential_id", UUID(as_uuid=True), nullable=True))
        op.create_foreign_key(
            "fk_test_cases_credential_id_credential_profiles",
            "test_cases",
            "credential_profiles",
            ["credential_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_index("ix_test_cases_credential_id", "test_cases", ["credential_id"])

    if "credential_role" not in tc_cols:
        op.add_column("test_cases", sa.Column("credential_role", sa.VARCHAR(100), nullable=True))

    tc_constraints = _constraints("test_cases")
    if "ck_test_cases_auth_mode" not in tc_constraints:
        op.create_check_constraint(
            "ck_test_cases_auth_mode",
            "test_cases",
            "auth_mode IN ('anonymous', 'login_flow', 'authenticated')",
        )

    tr_cols = _columns("test_results")
    if "run_id" not in tr_cols:
        op.add_column("test_results", sa.Column("run_id", UUID(as_uuid=True), nullable=True))
        op.create_foreign_key(
            "fk_test_results_run_id_test_runs",
            "test_results",
            "test_runs",
            ["run_id"],
            ["run_id"],
            ondelete="SET NULL",
        )
        op.create_index("ix_test_results_run_id", "test_results", ["run_id"])

    if "auth_states" not in _tables():
        op.create_table(
            "auth_states",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "project_id",
                UUID(as_uuid=True),
                sa.ForeignKey("projects.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "run_id",
                UUID(as_uuid=True),
                sa.ForeignKey("test_runs.run_id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "credential_id",
                UUID(as_uuid=True),
                sa.ForeignKey("credential_profiles.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("storage_state_path", sa.Text(), nullable=False, server_default=""),
            sa.Column("status", sa.VARCHAR(32), nullable=False, server_default="pending"),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.UniqueConstraint("run_id", "credential_id", name="uq_auth_states_run_credential"),
            sa.CheckConstraint(
                "status IN ('pending', 'ready', 'failed', 'expired')",
                name="ck_auth_states_status",
            ),
        )
        op.create_index("ix_auth_states_project_id", "auth_states", ["project_id"])
        op.create_index("ix_auth_states_run_id", "auth_states", ["run_id"])
        op.create_index("ix_auth_states_credential_id", "auth_states", ["credential_id"])

    # Keep updated_at useful without requiring application-side writes on every sync.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION set_credential_profiles_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_credential_profiles_updated_at ON credential_profiles;
        CREATE TRIGGER trg_credential_profiles_updated_at
        BEFORE UPDATE ON credential_profiles
        FOR EACH ROW EXECUTE FUNCTION set_credential_profiles_updated_at();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_credential_profiles_updated_at ON credential_profiles;")
    op.execute("DROP FUNCTION IF EXISTS set_credential_profiles_updated_at;")

    if "auth_states" in _tables():
        op.drop_table("auth_states")

    tr_cols = _columns("test_results")
    if "run_id" in tr_cols:
        op.drop_index("ix_test_results_run_id", table_name="test_results", if_exists=True)
        op.drop_constraint("fk_test_results_run_id_test_runs", "test_results", type_="foreignkey")
        op.drop_column("test_results", "run_id")

    tc_constraints = _constraints("test_cases")
    if "ck_test_cases_auth_mode" in tc_constraints:
        op.drop_constraint("ck_test_cases_auth_mode", "test_cases", type_="check")

    tc_cols = _columns("test_cases")
    if "credential_role" in tc_cols:
        op.drop_column("test_cases", "credential_role")
    if "credential_id" in tc_cols:
        op.drop_index("ix_test_cases_credential_id", table_name="test_cases", if_exists=True)
        op.drop_constraint(
            "fk_test_cases_credential_id_credential_profiles",
            "test_cases",
            type_="foreignkey",
        )
        op.drop_column("test_cases", "credential_id")
    if "auth_mode" in tc_cols:
        op.drop_index("ix_test_cases_auth_mode", table_name="test_cases", if_exists=True)
        op.drop_column("test_cases", "auth_mode")
    if "run_id" in tc_cols:
        op.drop_index("ix_test_cases_run_id", table_name="test_cases", if_exists=True)
        op.drop_constraint("fk_test_cases_run_id_test_runs", "test_cases", type_="foreignkey")
        op.drop_column("test_cases", "run_id")

    if "credential_profiles" in _tables():
        op.drop_table("credential_profiles")
