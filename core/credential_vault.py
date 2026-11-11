"""
Credential Vault — encrypts social account tokens at rest using Fernet.

Provides transparent encrypt/decrypt for any credential string.
Key rotation support is included for production environments.
Falls back to plaintext storage when no encryption key is configured
(single-tenant dev mode).
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger("kanyoza.core.credential_vault")

# Lazy import to avoid hard dependency at module level
_fernet_cls = None


def _get_fernet():
    global _fernet_cls
    if _fernet_cls is None:
        try:
            from cryptography.fernet import Fernet
            _fernet_cls = Fernet
        except ImportError:
            _fernet_cls = False  # Mark as unavailable
            logger.warning(
                "cryptography package not installed — CredentialVault will "
                "store tokens in plaintext. Run: pip install cryptography"
            )
    return _fernet_cls


class CredentialVault:
    """Encrypt / decrypt credential strings using Fernet symmetric encryption.

    Usage::

        vault = CredentialVault()          # reads key from settings
        encrypted = vault.encrypt("sk-abc123")
        original  = vault.decrypt(encrypted)  # "sk-abc123"
    """

    # Prefix to detect already-encrypted values
    _PREFIX = "enc::"
    # KDO-owned encryption-key marker; stripped before passing bytes to Fernet.
    _KEY_PREFIX = "KDO_ENCRYPTION_"

    def __init__(self, encryption_key: Optional[str] = None) -> None:
        if encryption_key is None:
            from config.settings import get_settings
            settings = get_settings()
            encryption_key = getattr(settings, "encryption_key", "")

        self._key = encryption_key or ""
        if self._key.startswith(self._KEY_PREFIX):
            self._key = self._key[len(self._KEY_PREFIX):]
        self._fernet = None

        Fernet = _get_fernet()
        if Fernet and self._key:
            try:
                self._fernet = Fernet(self._key.encode() if isinstance(self._key, str) else self._key)
                logger.info("CredentialVault initialised with Fernet encryption.")
            except Exception as exc:
                logger.error("Invalid encryption key — falling back to plaintext: %s", exc)
                self._fernet = None
        elif not self._key:
            logger.info("No ENCRYPTION_KEY configured — CredentialVault in plaintext mode.")

    @property
    def is_encrypted_mode(self) -> bool:
        return self._fernet is not None

    def encrypt(self, plaintext: str) -> str:
        """Encrypt a credential string. Returns prefixed ciphertext."""
        if not plaintext:
            return ""
        if plaintext.startswith(self._PREFIX):
            return plaintext  # Already encrypted

        if self._fernet is None:
            from config.settings import settings
            if settings.is_production:
                raise RuntimeError("Credential encryption required in production but Fernet key is not configured.")
            return plaintext  # Plaintext fallback for dev mode only

        try:
            token = self._fernet.encrypt(plaintext.encode("utf-8"))
            return f"{self._PREFIX}{token.decode('utf-8')}"
        except Exception as exc:
            logger.error("Encryption failed: %s", exc)
            from config.settings import settings
            if settings.is_production:
                raise RuntimeError(f"Encryption failed in production: {exc}") from exc
            return plaintext


    def decrypt(self, ciphertext: str) -> str:
        """Decrypt a credential string. Handles both encrypted and plain values."""
        if not ciphertext:
            return ""
        if not ciphertext.startswith(self._PREFIX):
            return ciphertext  # Not encrypted — return as-is

        if self._fernet is None:
            logger.warning("Cannot decrypt — no Fernet key configured.")
            return ""

        raw = ciphertext[len(self._PREFIX):]
        try:
            return self._fernet.decrypt(raw.encode("utf-8")).decode("utf-8")
        except Exception as exc:
            logger.error("Decryption failed: %s", exc)
            return ""

    def rotate_key(self, new_key: str, encrypted_values: list[str]) -> list[str]:
        """Re-encrypt a list of values with a new key.

        Returns a list of newly-encrypted values. The caller is responsible
        for persisting them back to the database.
        """
        Fernet = _get_fernet()
        if not Fernet:
            return encrypted_values

        # Decrypt with old key
        decrypted = [self.decrypt(v) for v in encrypted_values]

        # Build new vault
        new_vault = CredentialVault(encryption_key=new_key)

        # Re-encrypt with new key
        return [new_vault.encrypt(v) for v in decrypted]

    @staticmethod
    def generate_key() -> str:
        """Generate a KDO-prefixed Fernet key (prefix + base64 32-byte key)."""
        Fernet = _get_fernet()
        if Fernet:
            return CredentialVault._KEY_PREFIX + Fernet.generate_key().decode("utf-8")
        # Fallback: 32-byte random key base64-encoded
        import base64
        return CredentialVault._KEY_PREFIX + base64.urlsafe_b64encode(os.urandom(32)).decode("utf-8")


# Module-level singleton
credential_vault = CredentialVault()
