"""
IntentScanService — pre-flight abuse classifier.

Runs synchronously by callers awaiting scan_directive(); routed exclusively via OpenRouter.
"""

from __future__ import annotations

import json
import logging

import httpx

from config import Settings
from services.openrouter import chat_completions_json, openrouter_headers

log = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a security classifier for an autonomous AI agent platform.
Your only job is to determine if a user's directive describes malicious intent.

Malicious intent includes: credential stuffing, DDoS attacks, scraping private/gated data
without authorisation, spam campaigns, harassment, generating malware, bypassing security
systems for illegal access, or any other cyberattack vector.

Respond ONLY with valid JSON in this exact format:
{"flagged": true|false, "reason": "one sentence reason or null"}
"""


class IntentScanService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def scan_directive(self, directive: str) -> tuple[bool, str | None]:
        """
        Returns (flagged, reason).
        flagged=True means the directive must be rejected.
        Fails open if no OpenRouter key is configured, or if the API errors.
        """
        keys = self._settings.get_openrouter_keys()
        if not keys:
            return False, None

        key = keys[0]
        hdrs = openrouter_headers(
            key,
            self._settings.openrouter_http_referer,
            self._settings.openrouter_app_title,
        )
        models_to_try = [
            self._settings.openrouter_intent_scan_model,
            "openai/gpt-4o-mini",
            "anthropic/claude-3.5-haiku",
        ]

        data: dict | None = None
        for model in models_to_try:
            if not model:
                continue
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    data, _ = await chat_completions_json(
                        client,
                        url=self._settings.openrouter_base_url,
                        headers=hdrs,
                        model=model,
                        messages=[
                            {"role": "system", "content": _SYSTEM_PROMPT},
                            {"role": "user", "content": f"Directive: {directive}"},
                        ],
                        max_tokens=120,
                        temperature=0.0,
                    )
                break
            except (httpx.HTTPError, OSError) as exc:
                log.warning(
                    "intent_scan model '%s' failed via OpenRouter: %s",
                    model,
                    exc,
                )
                data = None

        if data is None:
            log.warning("intent_scan skipped after OpenRouter model fallback exhaustion")
            return False, None

        try:
            choices = data.get("choices") or []
            msg = (choices[0].get("message") or {}) if choices else {}
            text = msg.get("content") or ""
            result = json.loads(text)
            return bool(result["flagged"]), result.get("reason")
        except (json.JSONDecodeError, KeyError, IndexError, TypeError):
            return False, None
