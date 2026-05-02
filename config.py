"""Application settings — all env vars in one place (pydantic-settings v2)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ───────────────────────────────────────────────────────────────────
    app_base_url: str = "https://app.darwin.ai"
    environment: Literal["development", "staging", "production"] = "development"
    api_v1_prefix: str = "/api/v1"

    # ── Supabase ──────────────────────────────────────────────────────────────
    supabase_url: str = Field(...)
    supabase_anon_key: SecretStr = Field(...)
    supabase_service_role_key: SecretStr = Field(...)
    supabase_jwt_secret: SecretStr = Field(...)

    # ── PostgreSQL (direct asyncpg connection for FOR UPDATE locks) ───────────
    database_url: str = Field(
        ..., description="asyncpg DSN — postgresql://user:pass@host/db"
    )

    # ── Agent JWT ─────────────────────────────────────────────────────────────
    agent_jwt_secret: SecretStr = Field(
        ..., description="Separate from Supabase JWT — never expose to frontend"
    )
    agent_jwt_expire_hours: int = 2

    # ── VCC Encryption ────────────────────────────────────────────────────────
    vcc_encryption_key: SecretStr = Field(
        ..., description="Symmetric passphrase for pgcrypto pgp_sym_encrypt"
    )

    # ── Modal ─────────────────────────────────────────────────────────────────
    modal_token_id: SecretStr = Field(...)
    modal_token_secret: SecretStr = Field(...)
    modal_webhook_secret: SecretStr = Field(default="")

    # ── Telegram ──────────────────────────────────────────────────────────────
    telegram_bot_token: SecretStr = Field(...)
    telegram_admin_chat_id: str = Field(default="")

    # ── Stripe ────────────────────────────────────────────────────────────────
    stripe_webhook_secret: SecretStr = Field(...)

    # ── LangSmith / Phoenix ───────────────────────────────────────────────────
    langsmith_api_key: SecretStr = Field(default="")
    langchain_project: str = "darwin-production"

    # ── Browserbase ───────────────────────────────────────────────────────────
    browserbase_api_key: SecretStr = Field(default="")
    browserbase_project_id: str = Field(default="")

    # ── Redis ─────────────────────────────────────────────────────────────────
    redis_url: str = Field(default="redis://localhost:6379/0")

    # ── OpenAI (embeddings + intent scan fallback) ────────────────────────────
    openai_api_key: SecretStr = Field(default="")

    # ── LLM provider key pools (gateway multiplexing) ────────────────────────
    anthropic_api_key_1: SecretStr = Field(default="")
    anthropic_api_key_2: SecretStr = Field(default="")
    openai_api_key_1: SecretStr = Field(default="")
    openai_api_key_2: SecretStr = Field(default="")

    # ── CORS ──────────────────────────────────────────────────────────────────
    cors_origins: list[str] = ["http://localhost:3000"]

    # ── Telemetry circuit-breaker thresholds ──────────────────────────────────
    cb_burn_multiplier: float = 5.0       # 5× historical average → trip
    cb_failed_http_threshold: int = 10    # failed requests in 30s with no revenue
    cb_captcha_failures: int = 2          # consecutive CAPTCHA fails → human delegate
    low_balance_pct: float = 0.20         # 20% of initial budget → Telegram alert

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    def get_anthropic_keys(self) -> list[str]:
        keys = []
        for attr in ("anthropic_api_key_1", "anthropic_api_key_2"):
            val = getattr(self, attr).get_secret_value()
            if val:
                keys.append(val)
        return keys

    def get_openai_keys(self) -> list[str]:
        # Include the single key and pool keys
        keys = []
        for attr in ("openai_api_key", "openai_api_key_1", "openai_api_key_2"):
            val = getattr(self, attr).get_secret_value()
            if val:
                keys.append(val)
        return list(dict.fromkeys(keys))  # deduplicate preserving order


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
