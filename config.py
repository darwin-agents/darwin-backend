"""Application settings — all env vars in one place (pydantic-settings v2)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
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
    supabase_secret_key: SecretStr = Field(
        ...,
        description="Elevated server API key: sb_secret_... (preferred) or legacy JWT service_role",
    )
    supabase_jwt_secret: SecretStr = Field(
        ...,
        description="JWT secret for HS256 user access tokens (Dashboard → JWT Keys). "
        "Unused for asymmetric tokens; those are verified via JWKS.",
    )

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
    modal_webhook_secret: SecretStr = Field(default=SecretStr(""))
    modal_app_name: str = Field(
        default="darwin-agent",
        description="Modal app name containing the run function",
    )
    modal_function_name: str = Field(
        default="run_agent",
        description="Modal function name to spawn for Darwin sessions",
    )
    modal_environment_name: str | None = Field(
        default=None,
        description="Optional Modal environment override (e.g. dev/staging/main)",
    )

    # ── Telegram ──────────────────────────────────────────────────────────────
    telegram_bot_token: SecretStr = Field(...)
    telegram_admin_chat_id: str = Field(default="")

    # ── Stripe ────────────────────────────────────────────────────────────────
    stripe_webhook_secret: SecretStr = Field(...)

    # ── Langfuse (Mental Replay — matches Hermes observability/langfuse plugin) ─
    langfuse_public_key: SecretStr = Field(default=SecretStr(""))
    langfuse_secret_key: SecretStr = Field(default=SecretStr(""))
    langfuse_host: str = Field(
        default="https://cloud.langfuse.com",
        description="Langfuse API base (EU cloud, US/JP regions, or self-hosted origin)",
    )

    # ── Browserbase ───────────────────────────────────────────────────────────
    browserbase_api_key: SecretStr = Field(default=SecretStr(""))
    browserbase_project_id: str = Field(default="")

    # ── Redis ─────────────────────────────────────────────────────────────────
    redis_url: str = Field(default="redis://localhost:6379/0")

    # ── OpenRouter (single endpoint: chat, embeddings, multimodal) ────────────
    openrouter_api_key_1: SecretStr = Field(
        default=SecretStr(""),
        description="Primary OpenRouter API key — gateway, intent scan, embeddings",
    )
    openrouter_api_key_2: SecretStr = Field(default=SecretStr(""))  # optional pool key

    openrouter_base_url: str = Field(default="https://openrouter.ai/api/v1")

    # Optional attribution headers (OpenRouter recommends referer/title).
    openrouter_http_referer: str | None = Field(default=None)
    openrouter_app_title: str | None = Field(default=None)

    # Model fallbacks routed through OpenRouter (OpenRouter slugs, e.g. anthropic/claude-…).
    openrouter_intent_scan_model: str = Field(default="anthropic/claude-haiku-4-5-20260101")
    openrouter_embedding_model: str = Field(default="openai/text-embedding-3-small")

    # ── Spending guardrails ─────────────────────────────────────────────────────
    # Gateway: reject inference if ledger_balance <= this (unless disabled with negative).
    gateway_min_ledger_balance: float = Field(default=0.0)

    # Per-session cumulative LLM cost (telemetry-reported USD). 0 disables cap.
    session_llm_spend_cap_usd: float = Field(
        default=0.0,
        description="If > 0, reject /telemetry/llm-call once session cumulative exceeds cap",
    )

    # Reserved for gradual cutover tests (always OpenRouter-only when false — kept for tooling).
    use_openrouter: bool = Field(default=True, description="Must stay true — OpenRouter-only runtime")

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

    def get_openrouter_keys(self) -> list[str]:
        keys: list[str] = []
        for attr in ("openrouter_api_key_1", "openrouter_api_key_2"):
            val = getattr(self, attr).get_secret_value()
            if val:
                keys.append(val)
        return list(dict.fromkeys(keys))


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
