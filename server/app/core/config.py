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

    # LLM provider: "groq" or "nim" or "openrouter"
    llm_provider: str = "groq"

    groq_api_key: str | None = None
    groq_model: str = "llama-3.3-70b-versatile"
    groq_max_tokens: int = 1024

    # NVIDIA NIM (OpenAI-compatible)
    nim_api_key: str | None = None
    nim_model: str = "qwen/qwen2.5-coder-32b-instruct"
    nim_base_url: str = "https://integrate.api.nvidia.com/v1"
    nim_max_tokens: int = 2048

    # OpenRouter (OpenAI-compatible, free tier available)
    openrouter_api_key: str | None = None
    openrouter_model: str = "qwen/qwen3-coder:free"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_max_tokens: int = 1500

    scenario_agent_batch_chars: int = 4500
    scenario_agent_batch_size: int = 4
    scenario_agent_max_scenarios_per_batch: int = 5
    scenario_agent_batch_delay_seconds: float = 1.0
    scenario_dedup_max_chars: int = 8000

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

    # Playwright test credentials (passed as env vars to test subprocess)
    base_url: str = "http://localhost:3000"
    user_email: str = ""
    user_password: str = ""
    admin_email: str = ""
    admin_password: str = ""

    # Phase 3 — Test Execution
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672/"
    rabbitmq_queue: str = "phase3_test_jobs"
    chromium_workers: int = 3
    requeue_delay_ms: int = 15000
    test_timeout_ms: int = 60000
    vision_fallback: bool = False
    playwright_headed: bool = False
    state_json_path: str = "state.json"
    generated_scripts_dir: str = "tests/generated"

    # Seconds to sleep between grouped A5 LLM calls (rate-limit for free-tier models)
    # 8s = safe default for qwen3-coder:free (8 RPM) and groq (6 RPM)
    llm_rate_limit_sleep: float = 8.0

    # Path to Playwright auth storage state (created by auth.setup.ts before each run)
    auth_json_path: str = "tests/auth.json"

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
    def groq_api_keys(self) -> list[str]:
        if not self.groq_api_key:
            return []
        keys: list[str] = []
        for key in self.groq_api_key.split(","):
            normalized = key.strip()
            if normalized and normalized not in keys:
                keys.append(normalized)
        return keys

    @property
    def is_development(self) -> bool:
        return self.app_env.lower() in {"dev", "development", "local"}


@lru_cache
def get_settings() -> Settings:
    return Settings() # type: ignore

settings = get_settings()