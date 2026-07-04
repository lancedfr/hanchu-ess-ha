"""Tests for Hanchuess AES payload helpers."""

from __future__ import annotations

import base64

import pytest

from custom_components.hanchuess.crypto import _decrypt_payload, _encrypt_payload
from custom_components.hanchuess.const import AES_IV, AES_SECRET_KEY


def test_constants_are_valid_aes_material():
    assert len(AES_IV) == 16
    assert len(AES_SECRET_KEY) in (16, 24, 32)


def test_encrypt_decrypt_roundtrip():
    payload = {"account": "user@example.com", "pwd": "secret"}
    encrypted = _encrypt_payload(payload)

    assert isinstance(encrypted, str)
    assert base64.b64decode(encrypted)
    assert _decrypt_payload(encrypted) == payload


def test_encrypt_decrypt_unicode_roundtrip():
    payload = {"msg": "你好，Hanchu!", "count": 2}
    encrypted = _encrypt_payload(payload)

    assert _decrypt_payload(encrypted) == payload


def test_decrypt_known_ciphertext():
    payload = {"sn": "SN123456", "devType": "2", "maxCount": 1440}
    encrypted = _encrypt_payload(payload)

    assert encrypted == "sxIQthokFmXG7J6hPPlkEgr3lrdW248U9Rdg/F9SeEXnTtdSI9eih5TSCQXOxFLC"
    assert _decrypt_payload(encrypted) == payload


def test_wrong_key_raises():
    encrypted = _encrypt_payload({"x": 1})

    with pytest.raises(Exception):
        _decrypt_payload(encrypted, key=b"WrongKey12345678")
