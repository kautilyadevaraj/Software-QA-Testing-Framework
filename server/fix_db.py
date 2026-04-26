import os
from sqlalchemy import text
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.db.session import engine


def _migration_head() -> str:
    config = Config(os.path.join(os.path.dirname(__file__), "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    return script.get_current_head()


def fix():
    head = _migration_head()
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM alembic_version;"))
        conn.execute(text("INSERT INTO alembic_version (version_num) VALUES (:head);"), {"head": head})
    print(f"Alembic version fixed to {head}.")

if __name__ == "__main__":
    fix()
