# products/converter/routes.py
"""
MWK Converter API endpoints.
GET /api/v1/convert?from=USD&to=MWK&amount=100
GET /api/v1/currencies
"""

import logging
from flask import Blueprint, request, jsonify

from products.converter.models import ConversionRequest
from products.converter.service import CurrencyService
from products.shared.auth import require_api_key
from products.shared.rate_limiter import check_rate_limit

logger = logging.getLogger("kanyoza.products.converter.routes")

converter_bp = Blueprint("converter", __name__, url_prefix="/api/v1")
converter_service = CurrencyService()


@converter_bp.route("/convert", methods=["GET"])
@require_api_key
def convert_currency():
    """
    Convert currency.
    
    Query params:
        from: Source currency (e.g., USD)
        to: Target currency (e.g., MWK)
        amount: Amount to convert (e.g., 100)
    
    Headers:
        Authorization: Bearer kny_xxxxx
    """
    # Rate limiting
    api_key = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not check_rate_limit(api_key, "converter", max_requests=100, window_seconds=3600):
        return jsonify({"error": "Rate limit exceeded. Upgrade your plan."}), 429

    # Validate request
    try:
        conversion_request = ConversionRequest(
            **{k: v for k, v in request.args.items()}
        )
    except Exception as e:
        return jsonify({"error": f"Invalid request: {str(e)}"}), 400

    # Convert
    result = converter_service.convert(conversion_request)

    if result is None:
        return jsonify({
            "error": "Conversion failed. Check currency codes or try again later."
        }), 400

    # Log usage
    from products.shared.billing import track_usage
    track_usage(api_key, "converter")

    return jsonify(result.model_dump())


@converter_bp.route("/currencies", methods=["GET"])
def list_currencies():
    """List all supported currencies."""
    return jsonify({
        "currencies": converter_service.get_supported_currencies(),
        "count": len(converter_service.get_supported_currencies()),
    })


@converter_bp.route("/health/converter", methods=["GET"])
def health():
    """Health check for converter service."""
    return jsonify({
        "service": "converter",
        "status": "healthy" if converter_service.health_check() else "degraded",
    })
