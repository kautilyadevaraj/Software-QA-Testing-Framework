"""Unit tests for app.services.auth_service — user CRUD, authentication, token issuance."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.security import verify_password
from app.services.auth_service import (
    authenticate_user,
    create_user,
    get_user_by_email,
    issue_tokens_for_user,
)


class TestCreateUser:
    def test_persists_user(self, db_session: Session):
        user = create_user(db_session, "Alice@Example.COM", "Test@1234")
        assert user.id is not None
        assert user.email == "alice@example.com"  # lowercased
        assert user.role == "user"

    def test_hashes_password(self, db_session: Session):
        user = create_user(db_session, "bob@test.com", "Secret#99")
        assert user.password_hash != "Secret#99"
        assert verify_password("Secret#99", user.password_hash)


class TestGetUserByEmail:
    def test_found(self, db_session: Session):
        create_user(db_session, "found@test.com", "Test@1234")
        user = get_user_by_email(db_session, "found@test.com")
        assert user is not None
        assert user.email == "found@test.com"

    def test_not_found(self, db_session: Session):
        result = get_user_by_email(db_session, "nobody@test.com")
        assert result is None

    def test_case_insensitive_lookup(self, db_session: Session):
        create_user(db_session, "Mixed@Test.COM", "Test@1234")
        user = get_user_by_email(db_session, "mixed@test.com")
        assert user is not None


class TestAuthenticateUser:
    def test_success(self, db_session: Session):
        create_user(db_session, "auth@test.com", "Pass@word1")
        user = authenticate_user(db_session, "auth@test.com", "Pass@word1")
        assert user is not None
        assert user.email == "auth@test.com"

    def test_wrong_password(self, db_session: Session):
        create_user(db_session, "auth2@test.com", "Pass@word1")
        result = authenticate_user(db_session, "auth2@test.com", "WrongPass1!")
        assert result is None

    def test_nonexistent_email(self, db_session: Session):
        result = authenticate_user(db_session, "ghost@test.com", "Whatever1!")
        assert result is None


class TestIssueTokens:
    def test_returns_two_strings(self, test_user):
        access, refresh = issue_tokens_for_user(test_user.id)
        assert isinstance(access, str) and len(access) > 0
        assert isinstance(refresh, str) and len(refresh) > 0
        assert access != refresh
