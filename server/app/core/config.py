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
    credential_encryption_key: str | None = None
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

    # LLM provider: "anthropic" | "groq"
    llm_provider: str = "anthropic"

    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-4-6"
    anthropic_max_tokens: int = 4096

    groq_api_key: str | None = None
    groq_model: str = "llama-3.3-70b-versatile"
    groq_max_tokens: int = 1024

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
    RECORDINGS_BASE_PATH: str = "recordings"
    recorder_store_password_values: bool = False
    recorder_screenshot_indicator: bool = True

    # Playwright test credentials (passed as env vars to test subprocess)
    base_url: str = "http://localhost:3000"
    user_email: str = ""
    user_password: str = ""
    admin_email: str = ""
    admin_password: str = ""

    # Phase 3 — Test Execution
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672/"
    rabbitmq_queue: str = "phase3_test_jobs"
    rabbitmq_dlx: str = "phase3.dlx"
    rabbitmq_dlq: str = "phase3_test_jobs.dead"
    phase3_max_attempts: int = 3
    phase3_agent_retry_attempts: int = 3
    chromium_workers: int = 3
    phase3_embedded_workers: bool = True
    phase3_external_run_timeout_s: int = 3600
    requeue_delay_ms: int = 15000
    # ── Timeouts ───────────────────────────────────────────────────────────────
    # Historical setting: drove BOTH Playwright per-test timeout AND the worker
    # subprocess kill timer from the same value. Keeping it as a fallback for
    # legacy .env files; new code reads the split settings below.
    test_timeout_ms: int = 180000
    # Per-test Playwright timeout (a single test() invocation). Default 30s —
    # bails fast on hung selectors so a 6-test serial suite can fit inside the
    # subprocess wallclock. Falls back to test_timeout_ms when not explicitly
    # set so existing .env files keep working.
    playwright_test_timeout_ms: int = 0
    # Total subprocess wallclock for ONE Playwright run (the whole .spec.ts).
    # Default 600s. Must be ≥ playwright_test_timeout_ms × num_tests + buffer.
    # Falls back to test_timeout_ms × 4 when not explicitly set.
    worker_subprocess_timeout_ms: int = 0
    vision_fallback: bool = False
    playwright_headed: bool = False
    # Demo/local visibility: when headed mode is enabled, slow each Playwright
    # action so testers can actually watch the browser perform the flow.
    playwright_slow_mo_ms: int = 3000
    state_json_path: str = "state.json"
    generated_scripts_dir: str = "tests/generated"

    # Generic generated test data defaults. Projects with locale-sensitive
    # validation can override these without changing A3/A4/A5 code.
    phase3_test_data_name: str = "Test User"
    phase3_test_data_postal_code: str = "12345"
    phase3_test_data_phone: str = "9000000000"
    phase3_test_data_search: str = "test"
    phase3_test_data_email: str = "qa.user@example.test"

    # Auth-state cleanup — expired storageState files + DB rows
    auth_state_retention_hours: int = 24
    auth_setup_timeout_s: int = 90
    auth_state_cleanup_interval_minutes: int = 60
    auth_state_cleanup_enabled: bool = True

    # Generated-script cleanup — per-project per-run .spec.ts directories
    # under tests/generated/<project_id>/<run_id>/. Without this, disk fills
    # up forever as new runs land in new subdirectories. Kept slightly longer
    # than auth_state retention so traces / debug runs remain inspectable.
    script_retention_hours: int = 72
    script_cleanup_enabled: bool = True

    # A4 strict-snapshot: when True, missing DOM snapshots cause build_context
    # to raise instead of returning an empty stub. Empty-stub fallback masks a
    # real Phase-1 gap: A5 then writes scripts grounded on nothing and
    # hallucinates selectors. Enable in production. Default off for back-compat
    # so existing dev runs without complete snapshots keep working.
    a4_strict_snapshot: bool = False

    # Seconds to sleep between grouped A5 LLM calls (rate-limit for free-tier models)
    # 8s = safe default for qwen3-coder:free (8 RPM) and groq (6 RPM)
    llm_rate_limit_sleep: float = 8.0

    # LLM resilience — max in-flight calls, fallback chain, and retry policy
    llm_max_concurrent: int = 4
    llm_fallback_chain: str = "anthropic,groq"   # primary first, others as fallback
    llm_retry_attempts: int = 3
    llm_retry_backoff_base_s: float = 2.0

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

    @property
    def resolved_playwright_test_timeout_ms(self) -> int:
        """Per-test Playwright timeout. Prefers the explicit split setting;
        falls back to the legacy `test_timeout_ms` so old .env files keep working,
        and finally to a 30 000 ms (30 s) default."""
        if self.playwright_test_timeout_ms > 0:
            return self.playwright_test_timeout_ms
        if self.test_timeout_ms and self.test_timeout_ms != 180000:
            return self.test_timeout_ms
        return 30000

    @property
    def resolved_worker_subprocess_timeout_ms(self) -> int:
        """Subprocess-level kill timer for one Playwright run.

        Default 600 000 ms (10 min). When only the legacy `test_timeout_ms` is
        configured, scale it ×4 so a 180 s legacy value yields a 720 s budget —
        big enough to hold the per-test bail × all tests in the suite.
        """
        if self.worker_subprocess_timeout_ms > 0:
            return self.worker_subprocess_timeout_ms
        if self.test_timeout_ms and self.test_timeout_ms != 180000:
            return max(self.test_timeout_ms * 4, 600000)
        return 600000


@lru_cache
def get_settings() -> Settings:
    return Settings() # type: ignore

settings = get_settings()
