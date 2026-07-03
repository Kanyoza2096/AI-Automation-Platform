"""
API Key authentication.
Validates Bearer tokens against the Supabase api_keys table using SHA-256 hashing.
In development without Supabase, validation is strict-off (returns 401) so the
behaviour is always explicit — there is no mode that accepts forged tokens.
"""

import hashlib
import logging
import secrets
from functools import wraps
from typing import Optional

from flask import request, jsonify

logger = logging.getLogger("kanyoza.products.shared.auth")

_FORMAT_PREFIX = "kny_"
_FORMAT_MIN_LEN = 51  # "kny_" + 48 hex chars (secrets.token_hex(24))


def require_api_key(f):
    """Decorator that requires a valid API key in the Authorization header.

    Header format: ``Authorization: Bearer kny_<48-hex-chars>``

    The key is hashed with SHA-256 before the database lookup so the raw
    secret is never stored or logged.
    """

    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")

        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Missing or invalid Authorization header"}), 401

        api_key = auth_header[len("Bearer "):]

        if not _well_formed(api_key):
            return jsonify({"error": "Invalid API key format"}), 401

        if not _validate_key(api_key):
            return jsonify({"error": "Invalid or expired API key"}), 401

        return f(*args, **kwargs)

    return decorated


def _well_formed(api_key: str) -> bool:
    """Return True only when the key matches the expected format.

    Format: ``kny_`` followed by exactly 48 lowercase hex characters.
    """
    return (
        isinstance(api_key, str)
        and api_key.startswith(_FORMAT_PREFIX)
        and len(api_key) == _FORMAT_MIN_LEN
        and all(c in "0123456789abcdef" for c in api_key[len(_FORMAT_PREFIX):])
    )


def _validate_key(api_key: str) -> bool:
    """Validate an API key by hashing it and checking against Supabase.

    Returns False (deny) when Supabase is unavailable so there is no
    open backdoor in degraded mode — callers receive a 401.
    """
    key_hash = hash_api_key(api_key)

    try:
        from services.supabase import get_service_client
        client = get_service_client()
        if client is None:
            logger.warning(
                "auth: Supabase unavailable — rejecting API key request "
                "(set SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY to enable)"
            )
            return False

        result = (
            client.table("api_keys")
            .select("id, revoked")
            .eq("key_hash", key_hash)
            .maybe_single()
            .execute()
        )
        if result and result.data:
            if result.data.get("revoked"):
                logger.warning("auth: revoked API key presented (hash=%s…)", key_hash[:8])
                return False
            return True

        logger.warning("auth: unknown API key (hash=%s…)", key_hash[:8])
        return False

    except Exception as exc:
        logger.error("auth: key validation error — denying request: %s", exc)
        return False


def generate_api_key() -> str:
    """Generate a new cryptographically-random API key.

    Store only the hash (``hash_api_key(key)``) in the database.
    Return the raw key to the user exactly once.
    """
    return f"{_FORMAT_PREFIX}{secrets.token_hex(24)}"


def hash_api_key(api_key: str) -> str:
    """Return the SHA-256 hex digest of *api_key*. Used for storage and lookup."""
    return hashlib.sha256(api_key.encode()).hexdigest()
