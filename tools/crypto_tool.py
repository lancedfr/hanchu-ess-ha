#!/usr/bin/env python3
"""Small CLI helper for Hanchuess AES payload debugging.

Example usage:
py tools/crypto_tool.py encrypt '{"sn":"SN123456","devType":"2"}'
py tools/crypto_tool.py decrypt 'BASE64_CIPHERTEXT'
py tools/crypto_tool.py decrypt --pretty 'BASE64_CIPHERTEXT'

You can also pipe JSON or ciphertext through stdin:
Get-Content payload.json | py tools/crypto_tool.py encrypt
Get-Content cipher.txt | py tools/crypto_tool.py decrypt --pretty

Optional `--key-text` and `--iv-text` overrides are available when you need to
test non-default material.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from custom_components.hanchuess.const import AES_IV, AES_SECRET_KEY  # noqa: E402
from custom_components.hanchuess.crypto import _decrypt_payload, _encrypt_payload  # noqa: E402


def _decode_text(value: str | None, *, label: str) -> bytes:
    if value is None:
        return AES_SECRET_KEY if label == "key" else AES_IV
    raw = value.encode("utf-8")
    if label == "iv" and len(raw) != 16:
        raise ValueError("IV must be exactly 16 bytes")
    if label == "key" and len(raw) not in (16, 24, 32):
        raise ValueError("Key must be 16, 24, or 32 bytes")
    return raw


def _read_input(value: str | None, *, label: str) -> str:
    if value is not None:
        return value
    data = sys.stdin.read().strip()
    if not data:
        raise ValueError(f"{label} is required")
    return data


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Encrypt or decrypt Hanchuess AES payloads.")
    parser.add_argument(
        "--key-text",
        help="Optional AES key text; defaults to the integration key.",
    )
    parser.add_argument(
        "--iv-text",
        help="Optional AES IV text; defaults to the integration IV.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    encrypt = subparsers.add_parser("encrypt", help="Encrypt a JSON payload.")
    encrypt.add_argument(
        "payload",
        nargs="?",
        help="JSON payload string. If omitted, read JSON from stdin.",
    )

    decrypt = subparsers.add_parser("decrypt", help="Decrypt a Base64 ciphertext.")
    decrypt.add_argument(
        "ciphertext",
        nargs="?",
        help="Base64 ciphertext. If omitted, read from stdin.",
    )
    decrypt.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print the decrypted JSON output.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        key = _decode_text(args.key_text, label="key")
        iv = _decode_text(args.iv_text, label="iv")
    except ValueError as err:
        parser.error(str(err))

    if args.command == "encrypt":
        raw_payload = _read_input(args.payload, label="payload")
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError as err:
            parser.error(f"invalid JSON payload: {err.msg}")
        print(_encrypt_payload(payload, key=key, iv=iv))
        return 0

    raw_ciphertext = _read_input(args.ciphertext, label="ciphertext")
    payload = _decrypt_payload(raw_ciphertext, key=key, iv=iv)
    if args.pretty:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
