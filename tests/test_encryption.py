"""Tests for EncryptionService — VCC encrypt/decrypt round-trip."""

import base64
import os

import pytest

from app.services.encryption_service import EncryptionService


@pytest.fixture
def svc() -> EncryptionService:
    key = base64.b64encode(os.urandom(32)).decode()

    class _Settings:
        class vcc_encryption_key:
            @staticmethod
            def get_secret_value() -> str:
                return key

    return EncryptionService(_Settings())  # type: ignore[arg-type]


def test_encrypt_decrypt_roundtrip(svc: EncryptionService) -> None:
    secret = "4111111111111111"
    blob = svc.encrypt(secret)
    assert blob != secret
    assert svc.decrypt(blob) == secret


def test_different_nonces_each_call(svc: EncryptionService) -> None:
    blob1 = svc.encrypt("test")
    blob2 = svc.encrypt("test")
    assert blob1 != blob2  # nonces differ
