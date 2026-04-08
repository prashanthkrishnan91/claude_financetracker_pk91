"""Encryption service — AES-256-GCM for API key storage.

All user API keys are encrypted at the application layer before being
stored in Supabase. This ensures keys are never stored in plaintext,
even if the database is compromised.
"""

from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..config import get_settings


def _get_key() -> bytes:
    """Get the 32-byte AES key from settings."""
    settings = get_settings()
    return bytes.fromhex(settings.encryption_key)


def encrypt_value(plaintext: str) -> str:
    """Encrypt a string value with AES-256-GCM.

    Returns: base64-encoded string of (nonce || ciphertext || tag)
    """
    key = _get_key()
    nonce = os.urandom(12)  # 96-bit nonce for GCM
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    # Pack as nonce + ciphertext (tag is appended by AESGCM)
    return base64.b64encode(nonce + ciphertext).decode("ascii")


def decrypt_value(encrypted: str) -> str:
    """Decrypt an AES-256-GCM encrypted value.

    Input: base64-encoded string of (nonce || ciphertext || tag)
    Returns: original plaintext string
    """
    key = _get_key()
    raw = base64.b64decode(encrypted)
    nonce = raw[:12]
    ciphertext = raw[12:]
    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(nonce, ciphertext, None)
    return plaintext.decode("utf-8")
