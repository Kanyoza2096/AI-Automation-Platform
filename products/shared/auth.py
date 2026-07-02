# products/shared/auth.py
"""
API Key authentication.
Validates Bearer tokens against Supabase.
"""

import hashlib
import logging
from functools import wraps
from flask import request, jsonify

logger = logging.getLogger("kanyoza.products.shared.auth")


def require_api_key(f):
    """
    Decorator that requires a valid API key in the Authorization header.
    
    Header format: Authorization: Bearer kny_xxxxxxxxxxxx
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")

        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Missing or invalid Authorization header"}), 401

        api_key = auth_header.replace("Bearer ", "")

        if not api_key.startswith("kny_"):
            return jsonify({"error": "Invalid API key format"}), 401

        # In production, validate against Supabase
        if not _validate_key(api_key):
            return jsonify({"error": "Invalid or expired API key"}), 401

        return f(*args, **kwargs)

    return decorated


def _validate_key(api_key: str) -> bool:
    """
    Validate API key against database.
    For MVP, accepts any key starting with 'kny_'.
    In production, hash and check against Supabase.
    """
    # TODO: Production — validate against Supabase api_keys table
    return len(api_key) > 20 and api_key.startswith("kny_")


def generate_api_key() -> str:
    """Generate a new API key."""
    import secrets
    return f"kny_{secrets.token_hex(24)}"


def hash_api_key(api_key: str) -> str:
    """Hash API key for storage."""
    return hashlib.sha256(api_key.encode()).hexdigest()
