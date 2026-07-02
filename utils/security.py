"""
Security utilities — input validation, secret masking, and safe I/O helpers.

Also provides v9-compatible helpers (verify_facebook_signature, sanitize_message,
detect_abuse) so the existing webhook routes continue to work during the migration.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse


def mask_secret(value: str, visible_chars: int = 4) -> str:
    """
    Mask a secret string for safe logging.
    Shows the last *visible_chars* characters; replaces the rest with *.

    Example: mask_secret("ABCDEFGH", 4) → "****EFGH"
    """
    if not value:
        return "***"
    if len(value) <= visible_chars:
        return "*" * len(value)
    return "*" * (len(value) - visible_chars) + value[-visible_chars:]


def is_safe_url(url: str, allowed_schemes: tuple = ("https",)) -> bool:
    """
    Return True only if *url* uses one of the *allowed_schemes*.
    Guards against SSRF and open-redirect vulnerabilities.
    """
    try:
        parsed = urlparse(url)
        return parsed.scheme in allowed_schemes and bool(parsed.netloc)
    except Exception:
        return False


def sanitize_filename(name: str, max_length: int = 255) -> str:
    """
    Sanitize a filename by removing path traversal sequences and
    any characters that are unsafe on common filesystems.
    """
    # Remove path separators and null bytes
    name = re.sub(r"[/\\:*?\"<>|\x00]", "_", name)
    # Collapse repeated underscores
    name = re.sub(r"_+", "_", name)
    # Strip leading/trailing dots and spaces
    name = name.strip(". ")
    # Prevent empty names
    if not name:
        name = "unnamed"
    return name[:max_length]


def safe_read_env(key: str, default: str = "") -> str:
    """
    Read an environment variable.
    Strips surrounding whitespace that can sneak in from .env files.
    """
    return os.getenv(key, default).strip()


def constant_time_compare(a: str, b: str) -> bool:
    """
    Compare two strings in constant time to prevent timing attacks.
    Use for comparing tokens, secrets, and verify-tokens.
    """
    import hmac
    return hmac.compare_digest(a.encode(), b.encode())


def resolve_safe_path(base_dir: str, relative_path: str) -> Optional[Path]:
    """
    Resolve *relative_path* under *base_dir* and verify it does not
    escape the base directory (path traversal guard).
    Returns the resolved Path or None if the path is unsafe.
    """
    try:
        base = Path(base_dir).resolve()
        target = (base / relative_path).resolve()
        if base in target.parents or target == base:
            return target
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# v9 compatibility helpers (used by routes/webhook.py during migration)
# ---------------------------------------------------------------------------

# Patterns flagged as abusive / spam input
_ABUSE_PATTERNS = re.compile(
    r"(fuck|shit|ass|bitch|idiot|stupid|kill\s+your?self|hate\s+you)",
    re.IGNORECASE,
)

# Characters to strip from user messages
_STRIP_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def verify_facebook_signature(
    payload: bytes,
    signature_header: str,
    app_secret: str,
) -> bool:
    """
    Verify the X-Hub-Signature-256 HMAC sent by Facebook on every webhook POST.

    Returns True only when the HMAC matches exactly.
    Returns False — never True — when *app_secret* is empty or missing;
    an unconfigured secret must never allow unauthenticated webhook traffic.
    """
    if not app_secret:
        return False  # Refuse all requests — secret not configured
    if not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(
        app_secret.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()
    received = signature_header[len("sha256="):]
    return hmac.compare_digest(expected, received)


def sanitize_message(text: str) -> str:
    """
    Strip control characters and excessive whitespace from a user message.
    Returns a clean UTF-8 string safe for downstream processing.
    """
    text = _STRIP_CHARS.sub("", text)
    text = re.sub(r"[ \t]+", " ", text).strip()
    return text[:2000]  # Hard cap — no single message should exceed this


def detect_abuse(text: str) -> bool:
    """
    Return True when *text* contains content that should not be processed.
    Lightweight heuristic — replace with a proper moderation model in M4.
    """
    return bool(_ABUSE_PATTERNS.search(text))
