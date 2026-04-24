"""Unit tests for app.core.config — Settings validation and property methods."""

from __future__ import annotations

import os

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def _make_settings(**overrides) -> Settings:
    """Build a Settings instance with required fields + overrides."""
    defaults = {
        "DATABASE_URL": "sqlite://",
        "JWT_SECRET_KEY": "this_is_a_test_key_at_least_24_chars",
    }
    defaults.update(overrides)
    return Settings(**defaults)


class TestFrontendOrigins:
    def test_single_origin(self):
        s = _make_settings(FRONTEND_ORIGINS="http://localhost:3000")
        assert s.frontend_origins_list == ["http://localhost:3000"]

    def test_multiple_origins(self):
        s = _make_settings(FRONTEND_ORIGINS="http://a.com, http://b.com, http://c.com")
        assert s.frontend_origins_list == ["http://a.com", "http://b.com", "http://c.com"]

    def test_empty_origins(self):
        s = _make_settings(FRONTEND_ORIGINS="")
        assert s.frontend_origins_list == []

    def test_origins_with_extra_whitespace(self):
        s = _make_settings(FRONTEND_ORIGINS="  http://a.com  ,  http://b.com  ")
        assert s.frontend_origins_list == ["http://a.com", "http://b.com"]


class TestIsDevelopment:
    @pytest.mark.parametrize("env", ["development", "dev", "local"])
    def test_development_values(self, env: str):
        s = _make_settings(APP_ENV=env)
        assert s.is_development is True

    @pytest.mark.parametrize("env", ["production", "staging", "test"])
    def test_non_development_values(self, env: str):
        s = _make_settings(APP_ENV=env)
        assert s.is_development is False


class TestValidation:
    def test_cookie_samesite_valid_values(self):
        for value in ("lax", "strict", "none", "Lax", "STRICT"):
            s = _make_settings(COOKIE_SAMESITE=value)
            assert s.cookie_samesite in {"lax", "strict", "none"}

    def test_cookie_samesite_invalid_raises(self):
        with pytest.raises(ValidationError):
            _make_settings(COOKIE_SAMESITE="invalid")

    def test_jwt_secret_too_short_raises(self):
        with pytest.raises(ValidationError):
            _make_settings(JWT_SECRET_KEY="short")

    def test_defaults_are_sensible(self):
        s = _make_settings()
        assert s.api_prefix == "/api/v1"
        assert s.max_upload_mb == 20
        assert s.chunk_size_tokens == 512
        assert s.chunk_overlap_tokens == 64
