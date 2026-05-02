"""
EncryptionService — AES-256-GCM VCC encryption / decryption.

Raw PAN/CVV are NEVER persisted in plaintext. Decryption happens only in active
server memory and the plaintext is immediately purged after use.
"""

from __future__ import annotations

import base64
import os
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import Settings


class EncryptionService:
    def __init__(self, settings: Settings) -> None:
        raw_key = base64.b64decode(settings.vcc_encryption_key.get_secret_value())
        if len(raw_key) != 32:
            raise ValueError("vcc_encryption_key must be a base64-encoded 32-byte value.")
        self._aesgcm = AESGCM(raw_key)

    def encrypt(self, plaintext: str) -> str:
        """
        Encrypt a plaintext string.
        Returns a base64-encoded string: nonce(12 bytes) || ciphertext.
        """
        nonce = secrets.token_bytes(12)
        ct = self._aesgcm.encrypt(nonce, plaintext.encode(), None)
        return base64.b64encode(nonce + ct).decode()

    def decrypt(self, blob: str) -> str:
        """
        Decrypt a blob produced by :meth:`encrypt`.

        IMPORTANT: callers must zero / del the returned string from memory
        as soon as it is no longer needed.
        """
        raw = base64.b64decode(blob)
        nonce, ct = raw[:12], raw[12:]
        return self._aesgcm.decrypt(nonce, ct, None).decode()
