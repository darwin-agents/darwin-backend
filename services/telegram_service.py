"""
TelegramService — all Telegram Bot API interactions.

Decouples the bot API from business logic. All other services call this.
"""

from __future__ import annotations

import asyncpg
import httpx

from config import Settings


class TelegramService:
    def __init__(self, settings: Settings) -> None:
        self._token = settings.telegram_bot_token.get_secret_value()
        self._admin_chat_id = settings.telegram_admin_chat_id
        self._base = f"https://api.telegram.org/bot{self._token}"

    async def _post(self, method: str, data: dict) -> dict:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(f"{self._base}/{method}", json=data)
            r.raise_for_status()
            return r.json()

    async def _get_chat_id(self, pool: asyncpg.Pool, darwin_id: str) -> int | None:  # type: ignore[type-arg]
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT up.telegram_chat_id
                FROM user_profiles up
                JOIN darwins d ON d.user_id = up.id
                WHERE d.id = $1
                """,
                darwin_id,
            )
        return row["telegram_chat_id"] if row else None

    async def send_alert_by_darwin_id(
        self,
        pool: asyncpg.Pool,  # type: ignore[type-arg]
        darwin_id: str,
        message: str,
        screenshot_url: str | None = None,
    ) -> None:
        chat_id = await self._get_chat_id(pool, darwin_id)
        if not chat_id:
            return
        await self.send_message(chat_id, message)
        if screenshot_url:
            await self._post("sendPhoto", {"chat_id": chat_id, "photo": screenshot_url})

    async def send_message(self, chat_id: int | str, text: str) -> None:
        await self._post("sendMessage", {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
        })

    async def send_approval_request(
        self,
        chat_id: int | str,
        message: str,
        approve_payload: str,
        deny_payload: str,
        screenshot_url: str | None = None,
    ) -> int:
        """Send a message with [Approve] / [Deny] inline keyboard. Returns telegram message_id."""
        body = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
            "reply_markup": {
                "inline_keyboard": [[
                    {"text": "✅ Approve", "callback_data": approve_payload},
                    {"text": "❌ Deny", "callback_data": deny_payload},
                ]]
            },
        }
        result = await self._post("sendMessage", body)
        if screenshot_url:
            await self._post("sendPhoto", {"chat_id": chat_id, "photo": screenshot_url})
        return result["result"]["message_id"]

    async def send_captcha_link(
        self,
        chat_id: int | str,
        session_link: str,
        screenshot_url: str,
    ) -> None:
        message = (
            "🔐 <b>CAPTCHA intervention required</b>\n\n"
            "The agent has failed this CAPTCHA twice and is conserving capital "
            "by delegating to you.\n\n"
            f'<a href="{session_link}">👆 Tap here to solve the CAPTCHA</a>'
        )
        await self.send_message(chat_id, message)
        if screenshot_url:
            await self._post("sendPhoto", {"chat_id": chat_id, "photo": screenshot_url})

    async def send_admin_alert(self, message: str) -> None:
        if self._admin_chat_id:
            await self.send_message(self._admin_chat_id, message)
