from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── Application ──────────────────────────────────────────────────────────
    app_name: str = "Darwin Backend"
    app_version: str = "0.1.0"
    debug: bool = False
    environment: Literal["development", "staging", "production"] = "development"
    api_v1_prefix: str = "/api/v1"

    # ── Security ─────────────────────────────────────────────────────────────
    secret_key: SecretStr = Field(..., description="JWT signing secret")
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    agent_token_expire_minutes: int = 15  # short-lived tokens for agent↔god layer comms

    # ── Supabase ─────────────────────────────────────────────────────────────
    supabase_url: str = Field(..., description="Supabase project URL")
    supabase_key: SecretStr = Field(..., description="Supabase service role key")
    supabase_jwt_secret: SecretStr = Field(..., description="Supabase JWT secret")
    database_url: str = Field(..., description="PostgreSQL async connection string")

    # ── Redis ────────────────────────────────────────────────────────────────
    redis_url: str = Field(default="redis://localhost:6379/0")
    redis_max_connections: int = 20

    # ── Modal ────────────────────────────────────────────────────────────────
    modal_token_id: SecretStr = Field(..., description="Modal token ID")
    modal_token_secret: SecretStr = Field(..., description="Modal token secret")
    modal_app_name: str = "darwin-agent"
    modal_container_timeout_seconds: int = 900  # 15-minute hard OS-level kill

    # ── Stripe ───────────────────────────────────────────────────────────────
    stripe_secret_key: SecretStr = Field(...)
    stripe_webhook_secret: SecretStr = Field(...)

    # ── Telegram ─────────────────────────────────────────────────────────────
    telegram_bot_token: SecretStr = Field(...)
    telegram_webhook_secret: SecretStr = Field(default="")

    # ── Encryption ───────────────────────────────────────────────────────────
    vcc_encryption_key: SecretStr = Field(
        ..., description="AES-256 key for VCC encryption (base64-encoded 32 bytes)"
    )

    # ── CORS ─────────────────────────────────────────────────────────────────
    cors_origins: list[str] = ["http://localhost:3000"]

    # ── Celery ───────────────────────────────────────────────────────────────
    celery_broker_url: str = Field(default="redis://localhost:6379/1")
    celery_result_backend: str = Field(default="redis://localhost:6379/2")

    # ── Telemetry circuit-breaker thresholds ─────────────────────────────────
    metabolic_runaway_multiplier: float = 60.0  # 60× normal burn rate triggers breaker
    credential_stuffing_threshold: int = 50      # same field, same window → trip
    ddos_requests_per_minute: int = 1000         # requests to single domain → trip

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
