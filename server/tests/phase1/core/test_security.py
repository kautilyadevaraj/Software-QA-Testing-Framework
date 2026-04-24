"""Unit tests for app.core.security — password hashing, JWT tokens, cookies."""

from __future__ import annotations

from starlette.responses import Response
from starlette.testclient import TestClient

from app.core.security import (
    clear_auth_cookies,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    set_auth_cookies,
    verify_password,
)


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------


class TestPasswordHashing:
    def test_hash_password_produces_bcrypt_hash(self):
        hashed = hash_password("MyPassword1!")
        assert hashed.startswith("$2b$") or hashed.startswith("$2a$")

    def test_verify_password_correct(self):
        password = "CorrectHorse@1"
        hashed = hash_password(password)
        assert verify_password(password, hashed) is True

    def test_verify_password_wrong(self):
        hashed = hash_password("CorrectHorse@1")
        assert verify_password("WrongHorse@1", hashed) is False

    def test_hash_password_long_input(self):
        """bcrypt has a 72-byte limit but we SHA-256 pre-hash, so long passwords should work."""
        long_pw = "A" * 200
        hashed = hash_password(long_pw)
        assert verify_password(long_pw, hashed) is True


# ---------------------------------------------------------------------------
# JWT token encoding / decoding
# ---------------------------------------------------------------------------


class TestJWTTokens:
    def test_create_access_token_decodes(self):
        subject = "user-123"
        token = create_access_token(subject)
        payload = decode_token(token, expected_type="access")
        assert payload is not None
        assert payload["sub"] == subject
        assert payload["type"] == "access"

    def test_create_refresh_token_decodes(self):
        subject = "user-456"
        token = create_refresh_token(subject)
        payload = decode_token(token, expected_type="refresh")
        assert payload is not None
        assert payload["sub"] == subject
        assert payload["type"] == "refresh"

    def test_decode_token_wrong_type_returns_none(self):
        token = create_access_token("user-1")
        result = decode_token(token, expected_type="refresh")
        assert result is None

    def test_decode_token_invalid_string_returns_none(self):
        result = decode_token("garbage.not.a.token", expected_type="access")
        assert result is None

    def test_decode_token_empty_string_returns_none(self):
        result = decode_token("", expected_type="access")
        assert result is None

    def test_tokens_have_exp_and_iat(self):
        token = create_access_token("x")
        payload = decode_token(token, expected_type="access")
        assert "exp" in payload
        assert "iat" in payload


# ---------------------------------------------------------------------------
# Cookie helpers
# ---------------------------------------------------------------------------


class TestCookieHelpers:
    def test_set_auth_cookies_sets_both(self):
        response = Response()
        set_auth_cookies(response, "access_val", "refresh_val")
        cookie_headers = response.headers.getlist("set-cookie")
        assert len(cookie_headers) == 2
        joined = " ".join(cookie_headers)
        assert "access_token" in joined
        assert "refresh_token" in joined

    def test_clear_auth_cookies(self):
        response = Response()
        clear_auth_cookies(response)
        cookie_headers = response.headers.getlist("set-cookie")
        # Both cookies should be cleared (max-age=0 or expires in the past)
        assert len(cookie_headers) == 2
