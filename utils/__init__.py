"""
Kanyoza Systems AI Platform v10 — Utility modules.
Pure functions only — no state, no side effects, no service dependencies.
"""

from utils.ids import generate_correlation_id, correlation_id_ctx, get_current_correlation_id
from utils.text import truncate, sanitize_html, extract_hashtags, word_count
from utils.datetime_utils import utcnow, format_utc, parse_utc, humanize_delta
from utils.security import mask_secret, is_safe_url, sanitize_filename

__all__ = [
    # IDs
    "generate_correlation_id",
    "correlation_id_ctx",
    "get_current_correlation_id",
    # Text
    "truncate",
    "sanitize_html",
    "extract_hashtags",
    "word_count",
    # Datetime
    "utcnow",
    "format_utc",
    "parse_utc",
    "humanize_delta",
    # Security
    "mask_secret",
    "is_safe_url",
    "sanitize_filename",
]
