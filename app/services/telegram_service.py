"""
TelegramService — sends notifications and approval buttons to human creators.

Used by the God Layer's circuit breaker, ask_human tool, and VCC approval flow.
"""

from __future__ import annotations

import httpx

from app.config import Settings

_TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


class TelegramService:
    def __init__(self, settings: Settings) -> None:
        self._token = settings.telegram_bot_token.get_secret_value()

    async def send_message(self, chat_id: str, text: str) -> None:
        url = _TELEGRAM_API.format(token=self._token, method="sendMessage")
        async with httpx.AsyncClient() as client:
            await client.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"})

    async def send_approval_prompt(
        self,
        chat_id: str,
        message: str,
        approve_data: str,
        deny_data: str,
    ) -> None:
        """Send an inline keyboard with [Approve] / [Deny] buttons."""
        url = _TELEGRAM_API.format(token=self._token, method="sendMessage")
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
            "reply_markup": {
                "inline_keyboard": [
                    [
                        {"text": "✅ Approve", "callback_data": approve_data},
                        {"text": "❌ Deny", "callback_data": deny_data},
                    ]
                ]
            },
        }
        async with httpx.AsyncClient() as client:
            await client.post(url, json=payload)

    async def alert_circuit_breaker(self, chat_id: str, agent_id: str, burn_rate: float) -> None:
        text = (
            f"🚨 <b>Emergency Circuit Breaker</b> tripped on Agent <code>{agent_id}</code>\n"
            f"Burn rate: <b>${burn_rate:.4f}/hr</b> — container incinerated, VCC frozen."
        )
        await self.send_message(chat_id, text)
