# products/converter/routes.py
"""
MWK Converter API endpoints — LEGACY FLASK (NOT WIRED INTO FASTAPI).

Status: quarantined. This module uses Flask Blueprint and is NOT registered
on the FastAPI app. Do not import from main/lifecycle until migrated.

Intended routes (when migrated):
  GET /api/v1/convert          — Authenticated (API key required)
  GET /api/v1/convert/public   — Public (rate limited, no key)
  GET /api/v1/currencies       — Public
"""

# --- original flask implementation retained below for reference ---

import logging
from flask import Blueprint, request, jsonify

from products.converter.models import ConversionRequest
from products.converter.service import CurrencyService
from products.shared.auth import require_api_key
from products.shared.rate_limiter import check_rate_limit

logger = logging.getLogger("kanyoza.products.converter.routes")

converter_bp = Blueprint("converter", __name__, url_prefix="/api/v1")
converter_service = CurrencyService()


# ==================================================================
# PUBLIC — No API key needed
# ==================================================================

@converter_bp.route("/currencies", methods=["GET"])
def list_currencies():
    """List all supported currencies. Public, no auth required."""
    return jsonify({
        "currencies": converter_service.get_supported_currencies(),
        "count": len(converter_service.get_supported_currencies()),
    })


@converter_bp.route("/convert/public", methods=["GET"])
def convert_currency_public():
    """
    Public converter for demo/product pages.
    Rate limited per IP. No API key required.
    """
    client_ip = request.remote_addr or "unknown"
    if not check_rate_limit(client_ip, "converter_public", max_requests=30, window_seconds=3600):
        return jsonify({
            "error": "Rate limit exceeded. Get a free API key for unlimited access."
        }), 429

    try:
        conversion_request = ConversionRequest(
            **{k: v for k, v in request.args.items()}
        )
    except Exception as e:
        return jsonify({"error": f"Invalid request: {str(e)}"}), 400

    result = converter_service.convert(conversion_request)

    if result is None:
        return jsonify({
            "error": "Conversion failed. Check currency codes and try again."
        }), 400

    return jsonify(result.model_dump())


# ==================================================================
# AUTHENTICATED — API key required
# ==================================================================

@converter_bp.route("/convert", methods=["GET"])
@require_api_key
def convert_currency():
    """
    Convert currency. Requires valid API key.
    """
    api_key = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not check_rate_limit(api_key, "converter", max_requests=100, window_seconds=3600):
        return jsonify({
            "error": "Rate limit exceeded. Upgrade your plan for more requests."
        }), 429

    try:
        conversion_request = ConversionRequest(
            **{k: v for k, v in request.args.items()}
        )
    except Exception as e:
        return jsonify({"error": f"Invalid request: {str(e)}"}), 400

    result = converter_service.convert(conversion_request)

    if result is None:
        return jsonify({
            "error": "Conversion failed. Check currency codes and try again."
        }), 400

    from products.shared.billing import track_usage
    track_usage(api_key, "converter")

    return jsonify(result.model_dump())


# ==================================================================
# HEALTH
# ==================================================================

@converter_bp.route("/health/converter", methods=["GET"])
def health():
    """Health check for converter service."""
    return jsonify({
        "service": "converter",
        "status": "healthy" if converter_service.health_check() else "degraded",
    })