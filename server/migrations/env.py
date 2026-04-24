import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# ── Ensure the server root is on sys.path so app.* imports work ───────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# ── Import all models so SQLAlchemy's metadata is fully populated ─────────────
from app.db.base import Base  # noqa: E402
from app.models import (  # noqa: F401,E402
    User, Project, ProjectMember, ProjectFile,
    ApiEndpoint, DocumentChunk, IngestionJob,
)
from app.core.config import get_settings  # noqa: E402

# Alembic Config object — gives access to values in alembic.ini
config = context.config

# Override sqlalchemy.url from our Settings (reads DATABASE_URL from .env)
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)

# Set up Python logging from alembic.ini config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations without a live DB connection (useful for generating SQL scripts)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
