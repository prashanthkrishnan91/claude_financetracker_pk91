"""Tests for the encryption service — AES-256-GCM round-trip."""

from __future__ import annotations

import pytest

from app.services.crypto_service import decrypt_value, encrypt_value


class TestEncryptionRoundTrip:
    """Verify encrypt → decrypt produces original value."""

    def test_basic_string(self):
        original = "my-secret-api-key-12345"
        encrypted = encrypt_value(original)
        decrypted = decrypt_value(encrypted)
        assert decrypted == original

    def test_empty_string(self):
        original = ""
        encrypted = encrypt_value(original)
        decrypted = decrypt_value(encrypted)
        assert decrypted == original

    def test_unicode_string(self):
        original = "key-with-special-chars-@#$%^&*()"
        encrypted = encrypt_value(original)
        decrypted = decrypt_value(encrypted)
        assert decrypted == original

    def test_long_string(self):
        original = "x" * 10000
        encrypted = encrypt_value(original)
        decrypted = decrypt_value(encrypted)
        assert decrypted == original

    def test_different_encryptions_same_input(self):
        """Each encryption uses a random nonce — ciphertexts should differ."""
        original = "same-input"
        enc1 = encrypt_value(original)
        enc2 = encrypt_value(original)
        assert enc1 != enc2  # Different nonces
        assert decrypt_value(enc1) == decrypt_value(enc2) == original

    def test_tampered_ciphertext_fails(self):
        """Modifying ciphertext should cause decryption to fail."""
        encrypted = encrypt_value("secret")
        # Tamper with the ciphertext
        import base64
        raw = bytearray(base64.b64decode(encrypted))
        raw[-1] ^= 0xFF  # Flip last byte
        tampered = base64.b64encode(bytes(raw)).decode("ascii")
        with pytest.raises(Exception):
            decrypt_value(tampered)
