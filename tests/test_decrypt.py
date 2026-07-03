"""Tests for AES payload encryption/decryption helpers."""

import pytest

from custom_components.hanchuess.const import AES_IV, AES_SECRET_KEY
from custom_components.hanchuess.crypto import _decrypt_payload, _encrypt_payload


def test_roundtrip() -> None:
    original = {"account": "user@example.com", "pwd": "some-rsa-ciphertext"}
    encrypted = _encrypt_payload(original, AES_SECRET_KEY, AES_IV)
    recovered = _decrypt_payload(encrypted, AES_SECRET_KEY, AES_IV)
    assert recovered == original


def test_roundtrip_unicode() -> None:
    original = {"msg": "你好", "code": 200}
    encrypted = _encrypt_payload(original, AES_SECRET_KEY, AES_IV)
    recovered = _decrypt_payload(encrypted, AES_SECRET_KEY, AES_IV)
    assert recovered == original


def test_decrypt_known_ciphertext_flow() -> None:
    known_plain = {"station": "home", "power_kw": 3.7}
    known_cipher = _encrypt_payload(known_plain, AES_SECRET_KEY, AES_IV)
    result = _decrypt_payload(known_cipher, AES_SECRET_KEY, AES_IV)
    assert result["station"] == "home"
    assert result["power_kw"] == 3.7


def test_wrong_key_raises() -> None:
    encrypted = _encrypt_payload({"x": 1}, AES_SECRET_KEY, AES_IV)
    wrong_key = b"WrongKey12345678"
    with pytest.raises(Exception):
        _decrypt_payload(encrypted, wrong_key, AES_IV)
