"""Add high_level_scenarios table

Revision ID: 0003_add_high_level_scenarios
Revises: 0002_add_jira_tables
Create Date: 2026-04-25

"""

from typing import Sequence, Union

from alembic import op

revision: str = "0003_add_high_level_scenarios"
down_revision: Union[str, None] = "0002_add_jira_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS high_level_scenarios (
            id UUID PRIMARY KEY,
            project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            completed_by UUID NULL REFERENCES users(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_high_level_scenarios_source
                CHECK (source IN ('agent_1', 'agent_2', 'manual')),
            CONSTRAINT ck_high_level_scenarios_status
                CHECK (status IN ('pending', 'completed'))
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_high_level_scenarios_project_id ON high_level_scenarios (project_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_high_level_scenarios_status ON high_level_scenarios (status);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_high_level_scenarios_completed_by ON high_level_scenarios (completed_by);")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION set_updated_at()
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
        DROP TRIGGER IF EXISTS trg_high_level_scenarios_updated_at ON high_level_scenarios;

        CREATE TRIGGER trg_high_level_scenarios_updated_at
        BEFORE UPDATE ON high_level_scenarios
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_high_level_scenarios_updated_at ON high_level_scenarios;")
    op.execute("DROP INDEX IF EXISTS ix_high_level_scenarios_completed_by;")
    op.execute("DROP INDEX IF EXISTS ix_high_level_scenarios_status;")
    op.execute("DROP INDEX IF EXISTS ix_high_level_scenarios_project_id;")
    op.execute("DROP TABLE IF EXISTS high_level_scenarios;")
