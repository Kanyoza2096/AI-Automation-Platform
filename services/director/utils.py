"""
KANYOZA DIRECTOR v5.1 — "THE AUTONOMOUS CEO" (Enhanced Learning Edition)
=========================================================================

v5.1 CHANGES (Additive only — nothing removed):
  - ADDED: Context-aware learning (sector + capability tracking)
  - ADDED: Time-of-day pattern learning (morning/afternoon/evening/night)
  - ADDED: Recency-weighted confidence (7-day decay window)
  - ADDED: Duration tracking (spot slow capabilities)
  - ADDED: Automatic escalation for unreliable sector+capability combos
  - ADDED: Automatic skip for critically unreliable combos
  - ADDED: 7 new /learning/* audit endpoints
  - ADDED: Learning report embedded in status()
  - ADDED: 7 new /ooda/* endpoints (Autonomous Loop introspection)
  - ADDED: 11 new /meta/* endpoints (Meta Engine introspection)
  - All v5.0 features preserved (nothing removed)

v5.0 CHANGES (Additive only — nothing removed):
  - Async-native loop (asyncio.Task replaces threading.Thread)
  - Concurrent task execution (semaphore-limited)
  - Bounded task dict (prevents memory leak)
  - Health check integration (works with lifecycle manager)
  - Async Supabase storage (non-blocking)
  - Correlation ID tracking

RETAINED FROM V4.0 (Complete — nothing removed):
  - System Topology Model (25+ nodes)
  - Root Cause Analysis
  - Impact Assessment
  - Cascading Failure Prediction
  - Correlated Recovery
  - Learning Loop
  - Adaptive Thresholds
  - Predictive Failure Detection
  - Resource Throttling
  - Strategic Outlook
  - Resource Allocation
  - Executive Summary
  - Correlation Analysis
  - Initiatives Generation
  - Engine Auto-Discovery
  - Supabase Error Classification
  - Complete API (25+ endpoints)
  - Community Council Integration
  - Full Capability List (20+)
  - Daily Briefings
  - CLI Operation
"""

from __future__ import annotations

import argparse
import asyncio
import hmac
import inspect
import json
import logging
import os
import random
import re
import signal
import time
import uuid
from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from statistics import mean
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, TypeVar, Awaitable

from fastapi import Request as FastAPIRequest

log = logging.getLogger("kanyoza.director")

# Optional OpenTelemetry
try:
    from opentelemetry import trace
    tracer = trace.get_tracer("kanyoza.director", "5.1.0")
    HAS_TRACING = True
except ImportError:
    tracer = None
    HAS_TRACING = False

# Council Integration
try:
    from council.integration import get_council
    HAS_COUNCIL = True
except ImportError:
    HAS_COUNCIL = False


# Event Bus Integration
try:
    from core.unified_bus import get_unified_bus
    HAS_EVENT_BUS = True
except ImportError:
    HAS_EVENT_BUS = False


def _director_log_debug(msg: str, *args: Any) -> None:
    """Safe logger — uses logging directly so it works before `log` is bound."""
    logging.getLogger("kanyoza.director").debug(msg, *args)


async def _publish_event_async(event_name: str, payload: Dict[str, Any]) -> None:
    """Async publish on UnifiedEventBus."""
    if not HAS_EVENT_BUS:
        return
    pl = dict(payload or {})
    pl.setdefault("workspace_id", "platform")
    try:
        bus = await get_unified_bus()
        try:
            await bus.publish(event_name, pl, source="director", workspace_id="platform")
        except TypeError:
            await bus.publish(event_name, pl)
    except Exception as exc:
        _director_log_debug("Director publish failed for %s: %s", event_name, exc)


def _publish_event(event_name: str, payload: Dict[str, Any]) -> None:
    """Publish event — Director is async-native."""
    if not HAS_EVENT_BUS:
        return
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_publish_event_async(event_name, payload))
    except RuntimeError:
        _director_log_debug("Director: no running loop, skipping publish %s", event_name)


# ═══════════════════════════════════════════════════════════════════════════
# UTILITIES
# ═══════════════════════════════════════════════════════════════════════════

def now() -> datetime:
    return datetime.now(timezone.utc)

def utcnow() -> datetime:
    return datetime.now(timezone.utc)

def iso_now() -> str:
    return utcnow().isoformat()

def iso(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)

def safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, deque)):
        return [safe(v) for v in value]
    return str(value)

def clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))

def sanitize_error_message(exc: Exception, max_len: int = 300) -> str:
    raw = str(exc)
    raw = re.sub(r'File "[^"]*"', "File [redacted]", raw)
    raw = re.sub(r"/[^\s]*\.py", "[path_redacted]", raw)
    raw = re.sub(r"0x[0-9a-fA-F]+", "[addr]", raw)
    return raw[:max_len]

def generate_correlation_id() -> str:
    return uuid.uuid4().hex


# ═══════════════════════════════════════════════════════════════════════════
# SUPABASE ERROR CLASSIFIER
# ═══════════════════════════════════════════════════════════════════════════

class SupabaseErrorClassifier:
    """
    Classifies Supabase client errors into operator-actionable categories.

    In v5.1 the Director relies on this classifier to decide:
      - whether to retry a Supabase call,
      - whether to escalate to the Council,
      - whether to fail closed (reject the operation),
      - how to present the error in executive summaries.

    Categories are deliberately coarse. Fine-grained classification happens
    downstream in the stores layer where the specific Supabase call is known.
    """

    # Error categories, ordered by escalation severity
    TRANSIENT = "transient"          # network hiccups, timeouts — retry
    RATE_LIMITED = "rate_limited"    # 429 — back off, do not retry immediately
    AUTH = "auth"                    # 401/403 — escalate, do not retry
    NOT_FOUND = "not_found"          # 404 — caller error, no retry
    CONFLICT = "conflict"            # 409/unique violation — caller error
    SCHEMA = "schema"                # column/table mismatch — deploy issue
    PAYLOAD_TOO_LARGE = "payload_too_large"  # 413 — caller error
    SERVER = "server"                # 5xx — retry with backoff
    UNKNOWN = "unknown"              # anything else — log and escalate

    # HTTP status code → category
    _STATUS_MAP = {
        400: "UNKNOWN",           # caller error but not specific enough
        401: AUTH,
        403: AUTH,
        404: NOT_FOUND,
        409: CONFLICT,
        413: PAYLOAD_TOO_LARGE,
        422: SCHEMA,              # Supabase uses 422 for validation failures
        429: RATE_LIMITED,
        500: SERVER,
        502: SERVER,
        503: SERVER,
        504: SERVER,
    }

    # Supabase error message fragments → category
    _MESSAGE_PATTERNS = [
        (re.compile(r"timeout|timed out", re.IGNORECASE), TRANSIENT),
        (re.compile(r"connection (reset|refused|closed)", re.IGNORECASE), TRANSIENT),
        (re.compile(r"rate.?limit", re.IGNORECASE), RATE_LIMITED),
        (re.compile(r"JWT|invalid api key|apikey", re.IGNORECASE), AUTH),
        (re.compile(r"row[- ]level security|RLS", re.IGNORECASE), AUTH),
        (re.compile(r"duplicate key|unique constraint", re.IGNORECASE), CONFLICT),
        (re.compile(r"column .+ does not exist|relation .+ does not exist|schema", re.IGNORECASE), SCHEMA),
        (re.compile(r"payload too large|request entity too large", re.IGNORECASE), PAYLOAD_TOO_LARGE),
        (re.compile(r"5\d\d|internal server|bad gateway", re.IGNORECASE), SERVER),
    ]

    # Categories where a retry is appropriate
    _RETRYABLE = {TRANSIENT, SERVER, RATE_LIMITED}

    # Categories that indicate a governance or identity problem
    _ESCALATE = {AUTH, SCHEMA, UNKNOWN}

    @classmethod
    def classify(cls, error: Any) -> str:
        """
        Classify a Supabase error. Accepts either an Exception instance,
        a string, or an object with a `status`/`status_code` attribute.
        Never raises — returns UNKNOWN if classification itself fails.
        """
        try:
            if error is None:
                return cls.UNKNOWN

            # Try to extract a status code from common shapes
            status = None
            for attr in ("status", "status_code", "code", "http_status"):
                candidate = getattr(error, attr, None)
                if isinstance(candidate, int):
                    status = candidate
                    break
                if isinstance(candidate, str) and candidate.isdigit():
                    status = int(candidate)
                    break

            # Prefer status code if we have one
            if status is not None:
                if status in cls._STATUS_MAP:
                    return cls._STATUS_MAP[status]
                if 400 <= status < 500:
                    return cls.UNKNOWN
                if 500 <= status < 600:
                    return cls.SERVER

            # Fall back to message pattern matching
            message = str(error)
            for pattern, category in cls._MESSAGE_PATTERNS:
                if pattern.search(message):
                    return category

            return cls.UNKNOWN

        except Exception:
            # Classification must never raise. If it does, fail safe.
            return cls.UNKNOWN

    @classmethod
    def is_retryable(cls, category: str) -> bool:
        """True if a retry with backoff is appropriate for this category."""
        return category in cls._RETRYABLE

    @classmethod
    def should_escalate(cls, category: str) -> bool:
        """True if this category should be escalated to the Council."""
        return category in cls._ESCALATE

    @classmethod
    def describe(cls, category: str) -> str:
        """Human-readable description for executive summaries."""
        descriptions = {
            cls.TRANSIENT: "Transient network issue — the operation can be retried",
            cls.RATE_LIMITED: "Supabase rate limit reached — reduce request rate",
            cls.AUTH: "Authentication or authorization failure — check credentials and RLS",
            cls.NOT_FOUND: "Requested resource does not exist",
            cls.CONFLICT: "Conflict with an existing record",
            cls.SCHEMA: "Schema mismatch between the client and Supabase",
            cls.PAYLOAD_TOO_LARGE: "Request payload exceeds the allowed size",
            cls.SERVER: "Supabase server error — retry with backoff",
            cls.UNKNOWN: "Unclassified Supabase error",
        }
        return descriptions.get(category, descriptions[cls.UNKNOWN])


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC EXPORTS
# ═══════════════════════════════════════════════════════════════════════════

__all__ = [
    # Time helpers
    "now",
    "utcnow",
    "iso_now",
    "iso",
    # Data helpers
    "safe",
    "clamp",
    "sanitize_error_message",
    "generate_correlation_id",
    # Event publishing
    "_publish_event",
    "_publish_event_async",
    "_director_log_debug",
    # Error classification
    "SupabaseErrorClassifier",
    # Feature flags
    "HAS_TRACING",
    "HAS_COUNCIL",
    "HAS_EVENT_BUS",
    "tracer",
    # Types imported by sibling modules that expect them via this file historically
    "asyncio",
    "Counter",
    "defaultdict",
    "deque",
    "dataclass",
    "field",
    "asdict",
    "datetime",
    "timezone",
    "timedelta",
    "Enum",
    "mean",
    "uuid",
    "re",
    "json",
    "os",
]
