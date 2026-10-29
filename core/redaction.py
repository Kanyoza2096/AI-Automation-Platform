"""
core/redaction.py — Centralized secret and PII redaction.

Extracted from lifecycle._sanitize_error_message so both core.errors
and services.supabase can use it without creating an import cycle.

Contract:
  - Never raise. If a pattern fails, return the input unchanged (bounded).
  - Never assume the input is a str. Coerce safely.
  - Never grow the output beyond max_len.
"""

from __future__ import annotations

import re
from typing import Any, Final

# Ordered most-specific to least-specific. Order matters: the JWT pattern
# must run before the generic "long token" pattern, etc.
_REDACTIONS: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    # Traceback paths: keep the shape, drop the path.
    (re.compile(r'File "[^"]+", line \d+'), 'File "[redacted]", line [n]'),
    # POSIX absolute paths (home, Users, root, opt, var, srv, etc.)
    (re.compile(r"/(?:home|Users|root|opt|var|srv|etc|tmp)/[^\s:;,\"']+"), "[path]"),
    # Windows absolute paths
    (re.compile(r"[A-Za-z]:\\[^\s:;,\"']+"), "[path]"),
    # Hex addresses
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "[addr]"),
    # JWT-shaped tokens (header.payload.signature, base64url)
    (re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b"), "[jwt]"),
    # Well-known API key prefixes
    (re.compile(r"\b(?:sk|pk|rk|ghp|ghs|gho|ghu|xox[baprs]|glpat)-[A-Za-z0-9_\-]{10,}\b"), "[secret]"),
    # Supabase-style anon/service keys (JWT already covered, but legacy keys are base64)
    (re.compile(r"\bsb_[a-z0-9_]{20,}\b"), "[secret]"),
    # Authorization headers
    (re.compile(r"\bBearer\s+[A-Za-z0-9._\-+/=]+"), "Bearer [redacted]"),
    (re.compile(r"\bBasic\s+[A-Za-z0-9+/=]+"), "Basic [redacted]"),
    # Connection strings with embedded credentials
    (re.compile(r"(?:postgres|postgresql|mysql|redis|mongodb(?:\+srv)?)://[^\s\"']+"), "[connstr]"),
    # URL query strings that commonly carry keys
    (re.compile(r"([?&](?:api_?key|key|token|access_token|secret|password)=)[^&\s\"']+", re.IGNORECASE), r"\1[redacted]"),
    # Emails
    (re.compile(r"\b[\w.+\-]+@[\w\-]+\.[\w.\-]+\b"), "[email]"),
    # IPv4 addresses (avoid v6 for now — false positives are worse than misses)
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "[ip]"),
)

_DEFAULT_MAX_LEN: Final[int] = 200
_HARD_MAX_LEN: Final[int] = 4000


def _coerce_to_str(value: Any) -> str:
    """Best-effort str() that never raises."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return str(value)
    except Exception:
        return f"<unprintable {type(value).__name__}>"


def sanitize_error_message(value: Any, max_len: int = _DEFAULT_MAX_LEN) -> str:
    """Return a bounded, redacted string representation of `value`.

    Never raises. Never returns more than `max_len` characters.
    """
    if max_len < 1:
        max_len = 1
    if max_len > _HARD_MAX_LEN:
        max_len = _HARD_MAX_LEN

    raw = _coerce_to_str(value)
    try:
        for pattern, repl in _REDACTIONS:
            raw = pattern.sub(repl, raw)
    except Exception:
        # A regex failure must not propagate; return what we have.
        pass
    return raw[:max_len]


# Backwards-compatibility alias for existing imports of the private name.
_sanitize_error_message = sanitize_error_message


__all__ = ["sanitize_error_message", "_sanitize_error_message"]
