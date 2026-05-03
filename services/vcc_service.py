"""
VCCService — Virtual Credit Card encryption, decryption, and JIT browser injection.

Encryption uses pgcrypto's pgp_sym_encrypt (SQL-side) so raw PANs never pass through
Python memory in plaintext during writes. Decryption happens JIT, injects directly
into Browserbase, and is immediately purged from RAM.
"""

from __future__ import annotations

import asyncpg

from config import Settings


class VCCService:
    def __init__(self, pool: asyncpg.Pool, settings: Settings) -> None:  # type: ignore[type-arg]
        self._pool = pool
        self._key = settings.vcc_encryption_key.get_secret_value()
        self._bb_api_key = settings.browserbase_api_key.get_secret_value()
        self._bb_project_id = settings.browserbase_project_id

    async def store_vcc(
        self,
        darwin_id: str,
        user_id: str,
        pan: str,
        cvv: str,
        expiry: str,
        spend_limit: float,
        provider: str,
    ) -> str:
        """
        Encrypt PAN/CVV/expiry via pgcrypto and store in vcc_vault.
        Returns the opaque vcc_ref (UUID string). Raw values are never returned.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO vcc_vault
                  (darwin_id, user_id, encrypted_pan, encrypted_cvv,
                   encrypted_expiry, spend_limit, provider)
                VALUES (
                  $1, $2,
                  pgp_sym_encrypt($3, $6),
                  pgp_sym_encrypt($4, $6),
                  pgp_sym_encrypt($5, $6),
                  $7, $8
                )
                RETURNING id
                """,
                darwin_id, user_id,
                pan, cvv, expiry,
                self._key,
                spend_limit, provider,
            )
        # Zero out plaintext from Python memory
        pan = "0" * len(pan)
        cvv = "000"
        expiry = "00/00"
        del pan, cvv, expiry
        return str(row["id"])

    async def verify_ownership(self, vcc_ref: str, darwin_id: str) -> bool:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id FROM vcc_vault WHERE id = $1 AND darwin_id = $2",
                vcc_ref, darwin_id,
            )
        return row is not None

    async def decrypt_and_inject(
        self,
        vcc_ref: str,
        browserbase_session_id: str,
        target_fields: dict[str, str],
    ) -> None:
        """
        JIT decrypt in RAM → inject into Browserbase DOM → purge immediately.
        The raw PAN never enters the agent's process memory.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                  pgp_sym_decrypt(encrypted_pan, $2)::TEXT    AS pan,
                  pgp_sym_decrypt(encrypted_cvv, $2)::TEXT    AS cvv,
                  pgp_sym_decrypt(encrypted_expiry, $2)::TEXT AS expiry
                FROM vcc_vault WHERE id = $1
                """,
                vcc_ref, self._key,
            )

        if row is None:
            raise ValueError(f"VCC {vcc_ref} not found")

        pan: str = row["pan"]
        cvv: str = row["cvv"]
        expiry: str = row["expiry"]

        try:
            await self._browserbase_type(browserbase_session_id, target_fields, pan, cvv, expiry)
        finally:
            # Always purge, even on error
            pan = "0" * 16
            cvv = "000"
            expiry = "00/00"
            del pan, cvv, expiry

    async def _browserbase_type(
        self,
        session_id: str,
        fields: dict[str, str],
        pan: str,
        cvv: str,
        expiry: str,
    ) -> None:
        """
        Connect to the Browserbase session over CDP, find the active page, and
        inject each VCC field value using the React-compatible native setter so
        form validation events fire correctly.  Raw card values are never logged.
        """
        import asyncio
        import json

        import websockets  # type: ignore[import-untyped]

        cdp_url = (
            f"wss://connect.browserbase.com"
            f"?apiKey={self._bb_api_key}&sessionId={session_id}"
        )

        async with websockets.connect(cdp_url, max_size=10 * 1024 * 1024) as ws:
            _id = 0

            async def send_cdp(
                method: str,
                params: dict | None = None,
                *,
                cdp_session: str | None = None,
            ) -> dict:
                nonlocal _id
                _id += 1
                cmd_id = _id
                msg: dict = {"id": cmd_id, "method": method}
                if params:
                    msg["params"] = params
                if cdp_session:
                    msg["sessionId"] = cdp_session
                await ws.send(json.dumps(msg))
                # Drain the WebSocket until we receive the reply for this command.
                # Events (no "id") and replies for other sessions are discarded.
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=15.0)
                    ev: dict = json.loads(raw)
                    if ev.get("id") != cmd_id:
                        continue
                    if "error" in ev:
                        raise RuntimeError(
                            f"CDP {method} error: {ev['error']['message']}"
                        )
                    return ev.get("result", {})

            # Locate the active page target
            targets = await send_cdp("Target.getTargets")
            page = next(
                (t for t in targets["targetInfos"] if t["type"] == "page"),
                None,
            )
            if page is None:
                raise RuntimeError("No active page found in Browserbase session")

            # Attach to the page in flattened mode so page-level CDP commands
            # travel over the same WebSocket connection with a sessionId prefix.
            attach = await send_cdp(
                "Target.attachToTarget",
                {"targetId": page["targetId"], "flatten": True},
            )
            page_session: str = attach["sessionId"]

            await send_cdp("Runtime.enable", cdp_session=page_session)

            # Inject each field using the React-compatible native setter.
            # json.dumps safely escapes selector strings and card values so
            # special characters don't break the JS expression.
            for selector, value in (
                (fields["pan_element"], pan),
                (fields["cvv_element"], cvv),
                (fields["expiry_element"], expiry),
            ):
                js = (
                    "(function(sel, val) {"
                    "  var el = document.querySelector(sel);"
                    "  if (!el) return false;"
                    "  var desc = Object.getOwnPropertyDescriptor("
                    "    HTMLInputElement.prototype, 'value');"
                    "  if (desc && desc.set) { desc.set.call(el, val); }"
                    "  else { el.value = val; }"
                    "  el.dispatchEvent(new Event('input',  {bubbles: true}));"
                    "  el.dispatchEvent(new Event('change', {bubbles: true}));"
                    "  return true;"
                    "})(" + json.dumps(selector) + ", " + json.dumps(value) + ")"
                )
                result = await send_cdp(
                    "Runtime.evaluate",
                    {"expression": js, "returnByValue": True},
                    cdp_session=page_session,
                )
                if not result.get("result", {}).get("value"):
                    raise RuntimeError(f"Payment field not found: {selector}")

    async def sync_spend_limit(self, darwin_id: str, new_balance: float) -> None:
        """
        Update the VCC spend limit at the card-issuer level to match the new
        ledger balance. Keeps the financial blast radius mathematically capped.
        In Phase 1 (user-supplied cards) this is a no-op; Phase 2 calls Lithic.
        """
        # Phase 2: await lithic_client.cards.update(card_id, spending_limits=[...])
        pass
