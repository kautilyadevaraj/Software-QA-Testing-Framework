"""
Development utility: Creates the database and tables.

Usage (from server/):
    python init_db.py

For production use Alembic migrations instead:
    alembic upgrade head
"""
import sys
import os
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.core.config import get_settings

settings = get_settings()

# ── 1. Ensure the database exists ─────────────────────────────────────────────
print("Checking if sqat_db exists...")

_db_url = settings.database_url  # postgresql://user:pass@host:port/dbname
_parts = _db_url.replace("postgresql://", "").split("@")
_user_pass = _parts[0].split(":")
_host_port_db = _parts[1].split("/")
_host_port = _host_port_db[0].split(":")

_pg_user = _user_pass[0]
_pg_pass = _user_pass[1] if len(_user_pass) > 1 else ""
_pg_host = _host_port[0]
_pg_port = int(_host_port[1]) if len(_host_port) > 1 else 5432
_pg_db   = _host_port_db[1] if len(_host_port_db) > 1 else "sqat_db"

try:
    conn = psycopg2.connect(
        dbname="postgres",
        user=_pg_user,
        password=_pg_pass,
        host=_pg_host,
        port=_pg_port,
    )
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_catalog.pg_database WHERE datname = %s", (_pg_db,))
    if not cur.fetchone():
        print(f"Database '{_pg_db}' does not exist — creating...")
        cur.execute(f'CREATE DATABASE "{_pg_db}"')
        print(f"Database '{_pg_db}' created.")
    else:
        print(f"Database '{_pg_db}' already exists.")
    cur.close()
    conn.close()
except Exception as exc:
    print(f"Warning: could not auto-create database: {exc}")
    print("Make sure PostgreSQL is running and DATABASE_URL in .env is correct.")

# ── 2. Create tables ───────────────────────────────────────────────────────────
from app.db.session import engine
from app.db.base import Base
from app.models.user import User  # noqa: F401
from app.models.project import Project, ProjectMember, ProjectFile  # noqa: F401

print("Creating database tables...")
Base.metadata.create_all(bind=engine)
print("Tables ready.")

print("\nDone! Database is ready.")
