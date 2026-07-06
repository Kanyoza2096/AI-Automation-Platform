# products/shared/auth.py
"""
API key authentication helpers for product routes.
"""

import logging
from functools import wraps
from flask import request, jsonify
from config.settings import settings

logger = logging.getLogger("kanyoza.products.shared.auth")


def require_api_key(f):
    """
    Decorator that enforces Bearer token authentication.
    Checks Authorization: Bearer <token> header against MASTER_API_TOKEN.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        token = auth_header.replace("Bearer ", "").strip()

        if not token:
            return jsonify({"error": "Missing API key. Use Authorization: Bearer <key>"}), 401

        master = settings.master_api_token
        if master and token != master:
            return jsonify({"error": "Invalid API key."}), 403

        return f(*args, **kwargs)
    return decorated
