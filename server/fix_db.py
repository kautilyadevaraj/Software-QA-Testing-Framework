import os
from sqlalchemy import text
from app.db.session import engine

def fix():
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM alembic_version;"))
        conn.execute(text("INSERT INTO alembic_version (version_num) VALUES ('0001_consolidated');"))
    print("Alembic version fixed.")

if __name__ == "__main__":
    fix()
