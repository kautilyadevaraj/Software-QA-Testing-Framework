from __future__ import annotations

from functools import lru_cache

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "SQAT Backend Service"
    app_env: str = "development"
    api_prefix: str = "/api/v1"

    database_url: str

    jwt_secret_key: str = Field(
        validation_alias=AliasChoices("JWT_SECRET_KEY", "JWT_SECRET", "jwt_secret_key", "jwt_secret")
    )
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    cookie_secure: bool = False
    cookie_samesite: str = "lax"
    cookie_domain: str | None = None
    access_cookie_name: str = "access_token"
    refresh_cookie_name: str = "refresh_token"

    frontend_origins: str = "http://localhost:3000"

    max_upload_mb: int = 20
    upload_dir: str = "uploads"

    rate_limit_auth: str = "300/minute"
    rate_limit_api: str = "5000/minute"

    qdrant_url: str | None = None
    qdrant_api_key: str | None = None

    hf_token: str | None = None
    hf_model_name: str = "sentence-transformers/all-MiniLM-L6-v2"
    hf_models_dir: str = "models"

    # Jira Integration
    jira_base_url: str | None = None
    jira_email: str | None = None
    jira_api_token: str | None = None
    jira_lead_account_id: str | None = None
    
    PUBLIC_API_URL: str = "http://localhost:8000"   # override with actual VM URL in .env
    RECORDINGS_BASE_PATH: str = "uploads/recordings"

    @field_validator("cookie_samesite")
    @classmethod
    def validate_cookie_samesite(cls, value: str) -> str:
        normalized = value.lower()
        if normalized not in {"lax", "strict", "none"}:
            raise ValueError("COOKIE_SAMESITE must be one of: lax, strict, none")
        return normalized

    @field_validator("jwt_secret_key")
    @classmethod
    def validate_secret_length(cls, value: str) -> str:
        if len(value) < 24:
            raise ValueError("JWT_SECRET_KEY must be at least 24 characters")
        return value

    @property
    def frontend_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.frontend_origins.split(",") if origin.strip()]

    @property
    def is_development(self) -> bool:
        return self.app_env.lower() in {"dev", "development", "local"}


@lru_cache
def get_settings() -> Settings:
    return Settings() # type: ignore

settings = get_settings()