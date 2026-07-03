"""AES payload helpers for future encrypted request support."""

from __future__ import annotations

import base64
import json
from typing import Any

from .const import AES_IV, AES_SECRET_KEY


def _encrypt_payload(data: dict[str, Any], key: bytes = AES_SECRET_KEY, iv: bytes = AES_IV) -> str:
    """Serialize payload to JSON and encrypt with AES-CBC + PKCS7 padding."""
    from cryptography.hazmat.primitives import padding as sym_padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
    padder = sym_padding.PKCS7(128).padder()
    padded = padder.update(raw) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(encrypted).decode("utf-8")


def _decrypt_payload(encrypted_b64: str, key: bytes = AES_SECRET_KEY, iv: bytes = AES_IV) -> dict[str, Any]:
    """Decode Base64 AES-CBC ciphertext and return parsed JSON payload."""
    from cryptography.hazmat.primitives import padding as sym_padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    ciphertext = base64.b64decode(encrypted_b64)
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = sym_padding.PKCS7(128).unpadder()
    plain = unpadder.update(padded) + unpadder.finalize()
    return json.loads(plain.decode("utf-8"))
