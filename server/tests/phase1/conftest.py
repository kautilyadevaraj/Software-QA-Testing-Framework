"""Shared test fixtures — in-memory SQLite database, FastAPI TestClient, and helpers.

PostgreSQL-specific types (UUID, ARRAY, JSONB) are compiled to SQLite-compatible
equivalents via SQLAlchemy compiler hooks so the full ORM model works unchanged.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import String, Text, create_engine, event
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.types import TypeDecorator

# ---------------------------------------------------------------------------
# Environment overrides — MUST come BEFORE any app imports so Settings reads
# them instead of the real .env values.
# ---------------------------------------------------------------------------
os.environ["APP_ENV"] = "testing"
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["JWT_SECRET_KEY"] = "test_secret_key_at_least_24_chars_long"
os.environ["FRONTEND_ORIGINS"] = "http://localhost:3000"
os.environ["QDRANT_HOST"] = "localhost"
os.environ["QDRANT_PORT"] = "6333"
os.environ["QDRANT_COLLECTION"] = "test_sqat_chunks"
os.environ["EMBEDDING_MODEL"] = "all-MiniLM-L6-v2"

# Force clear the lru_cache on get_settings so it picks up our env vars
from app.core.config import get_settings  # noqa: E402

get_settings.cache_clear()

from app.db.base import Base  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.project import Project, ProjectMember, ProjectRole, ProjectStatus  # noqa: E402
from app.core.security import hash_password, create_access_token  # noqa: E402

# ---------------------------------------------------------------------------
# SQLite ↔ PostgreSQL type compatibility layer
# ---------------------------------------------------------------------------
# SQLite has no native ARRAY, JSONB, or UUID types.  We register compiler
# hooks so that SQLAlchemy emits TEXT for these columns when the dialect is
# 'sqlite', while leaving PostgreSQL behaviour untouched.

from sqlalchemy.ext.compiler import compiles  # noqa: E402


@compiles(UUID, "sqlite")
def _compile_uuid_sqlite(type_, compiler, **kw):
    return "VARCHAR(36)"


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(type_, compiler, **kw):
    return "TEXT"


@compiles(ARRAY, "sqlite")
def _compile_array_sqlite(type_, compiler, **kw):
    return "TEXT"


# ---------------------------------------------------------------------------
# Custom event listeners so ARRAY / JSONB columns round-trip through JSON
# serialization on SQLite.
# ---------------------------------------------------------------------------
# (For simple test assertions this is sufficient; we're not testing the DB
# dialect itself — we're testing application logic.)

# ---------------------------------------------------------------------------
# SQLite in-memory engine with FK support
# ---------------------------------------------------------------------------

from sqlalchemy.pool import StaticPool

TEST_ENGINE = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(TEST_ENGINE, "connect")
def _enable_fk(dbapi_conn, _connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


TestingSessionLocal = sessionmaker(
    bind=TEST_ENGINE,
    autoflush=False,
    autocommit=False,
    class_=Session,
)

# ---------------------------------------------------------------------------
# Temporary directories for uploads / extracted text
# ---------------------------------------------------------------------------

_TMP_DIR = tempfile.mkdtemp(prefix="sqat_test_")
_UPLOAD_DIR = os.path.join(_TMP_DIR, "uploads")
_EXTRACTED_DIR = os.path.join(_TMP_DIR, "extracted")
os.makedirs(_UPLOAD_DIR, exist_ok=True)
os.makedirs(_EXTRACTED_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _patch_settings(monkeypatch):
    """Override settings paths for every test so files land in temp dirs."""
    settings = get_settings()
    monkeypatch.setattr(settings, "upload_dir", _UPLOAD_DIR)
    monkeypatch.setattr(settings, "extracted_text_dir", _EXTRACTED_DIR)


@pytest.fixture(autouse=True)
def _patch_bcrypt_rounds(monkeypatch):
    """Make bcrypt fast during tests by using minimum rounds."""
    import bcrypt
    original_gensalt = bcrypt.gensalt
    def fast_gensalt(rounds=4, prefix=b"2b"):
        return original_gensalt(rounds, prefix)
    monkeypatch.setattr(bcrypt, "gensalt", fast_gensalt)


@pytest.fixture(autouse=True)
def _setup_tables():
    """Create all tables before each test, drop after."""
    Base.metadata.create_all(bind=TEST_ENGINE)
    yield
    Base.metadata.drop_all(bind=TEST_ENGINE)


@pytest.fixture()
def db_session() -> Generator[Session, None, None]:
    """Provide a transactional DB session for unit tests."""
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


def _override_get_db():
    """Dependency override for FastAPI — uses test DB session."""
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
    """Unauthenticated FastAPI test client with DB override."""
    fastapi_app.dependency_overrides[get_db] = _override_get_db
    with TestClient(fastapi_app, raise_server_exceptions=False) as c:
        yield c
    fastapi_app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# User / auth helpers
# ---------------------------------------------------------------------------

TEST_EMAIL = "testuser@example.com"
TEST_PASSWORD = "Test@1234"


@pytest.fixture()
def test_user(db_session: Session) -> User:
    """Create and persist a test user."""
    user = User(
        email=TEST_EMAIL,
        password_hash=hash_password(TEST_PASSWORD),
        role="user",
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _make_auth_headers(user_id: uuid.UUID) -> dict[str, str]:
    token = create_access_token(str(user_id))
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def auth_headers(test_user: User) -> dict[str, str]:
    """Return Authorization header dict for the test user."""
    return _make_auth_headers(test_user.id)


@pytest.fixture()
def auth_client(client: TestClient, auth_headers: dict[str, str]) -> TestClient:
    """TestClient with auth headers pre-set on every request."""
    client.headers.update(auth_headers)
    return client


# ---------------------------------------------------------------------------
# Project helper
# ---------------------------------------------------------------------------


@pytest.fixture()
def test_project(db_session: Session, test_user: User) -> Project:
    """Create a test project owned by the test user."""
    project = Project(
        owner_id=test_user.id,
        name="Test Project",
        description="A test project",
        status=ProjectStatus.DRAFT,
        url="",
    )
    owner_member = ProjectMember(user_id=test_user.id, role=ProjectRole.OWNER)
    project.members.append(owner_member)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return project


# ---------------------------------------------------------------------------
# Second user helper (for member tests)
# ---------------------------------------------------------------------------

SECOND_EMAIL = "second@example.com"
SECOND_PASSWORD = "Second@1234"


@pytest.fixture()
def second_user(db_session: Session) -> User:
    """Create a second test user."""
    user = User(
        email=SECOND_EMAIL,
        password_hash=hash_password(SECOND_PASSWORD),
        role="user",
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user
