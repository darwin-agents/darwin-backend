"""
IntentScanService — pre-flight abuse classifier.

Called synchronously by /darwins/spawn before any provisioning.
Uses Claude Haiku for fast, cheap classification of malicious directives.
"""

from __future__ import annotations

import json

import anthropic

from config import Settings

_SYSTEM_PROMPT = """
You are a security classifier for an autonomous AI agent platform.
Your only job is to determine if a user's directive describes malicious intent.

Malicious intent includes: credential stuffing, DDoS attacks, scraping private/gated data
without authorisation, spam campaigns, harassment, generating malware, bypassing security
systems for illegal access, or any other cyberattack vector.

Respond ONLY with valid JSON in this exact format:
{"flagged": true|false, "reason": "one sentence reason or null"}
"""


class IntentScanService:
    def __init__(self, settings: Settings) -> None:
        keys = settings.get_anthropic_keys()
        key = keys[0] if keys else ""
        self._client = anthropic.Anthropic(api_key=key) if key else None

    async def scan_directive(self, directive: str) -> tuple[bool, str | None]:
        """
        Returns (flagged, reason).
        flagged=True means the directive must be rejected.
        Falls back to False if no API key is configured (dev mode).
        """
        if self._client is None:
            return False, None

        response = self._client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"Directive: {directive}"}],
        )
        try:
            result = json.loads(response.content[0].text)
            return bool(result["flagged"]), result.get("reason")
        except (json.JSONDecodeError, KeyError):
            # If classifier output is unparseable, fail open (do not block)
            return False, None
