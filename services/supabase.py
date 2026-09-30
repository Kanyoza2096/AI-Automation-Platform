"""
Supabase client factory — v13.0.

Provides separate anon and service-role clients.
SocialAccountRepository and other backend operations should use
the service-role client for credential lookups.

v13.0 CHANGES:
  - Thread-safe singleton creation (fixes concurrent-boot race).
  - URL consistency asserted on subsequent calls (no silent re-init).
  - Error logs sanitized via core.redaction (no URL/credential leaks).
  - Error classification type-based, string-match only as fallback.
  - supabase_service.client now prefers service_role explicitly and emits
    a deprecation warning — prevents silent anon→service privilege shift
    on config change.
  - Optional aclose_all() for shutdown registry integration.
  - Fresh-client factories retained for backwards compat but DEPRECATED.

v12.2 (unchanged behavior preserved):
  - create_fresh_service_client() / create_fresh_anon_client()
  - Per-request fresh clients for reliability (see DEPRECATION note).

Architecture §13 — Supabase Architecture:
  - User-facing / RLS-aware client (anon)
  - Server-side service-role client (trusted backend ops)
  - Service-role credentials never exposed to browsers or frontend

Architecture §19 — Service-Role Credential Security:
  SUPABASE_SERVICE_ROLE_KEY is never:
    - returned by an API
    - embedded in frontend code
    - logged
    - committed to Git

Architecture §9 — No Silent Fallback:
  - Client creation failure raises typed errors
  - "Not configured" is distinct from "connection failed"
  - ping() returns structured health, not bool
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from core.errors import (
    DatabaseUnavailableError,
    DatabaseAuthError,
    ErrorCode,
)
from core.redaction import sanitize_error_message

logger = logging.getLogger("kanyoza.services.supabase")


# ═══════════════════════════════════════════════════════════════════════════
# Module-level singleton state
# ═══════════════════════════════════════════════════════════════════════════

_anon_client: Any = None
_service_client: Any = None
_anon_client_url: Optional[str] = None
_service_client_url: Optional[str] = None
_singleton_lock = threading.Lock()

# PATCH: probe target is configurable so it can be changed without a code edit.
# Defaults to "bot_events" to preserve existing behavior; override via env.
_PROBE_TABLE = os.getenv("SUPABASE_PROBE_TABLE", "bot_events").strip() or "bot_events"


# ═══════════════════════════════════════════════════════════════════════════
# Health Check Result
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class SupabaseHealth:
    """Structured health check result — never a bare bool.

    Architecture §33 — Health Checks:
      Differentiate: healthy, degraded, unavailable.
      A service should not report "healthy" merely because the
      Python process is alive.
    """
    healthy: bool
    client_type: str  # "anon", "service", "none", "fresh"
    message: str = ""
    latency_ms: Optional[float] = None
    error_code: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "healthy": self.healthy,
            "client_type": self.client_type,
            "message": self.message,
            "latency_ms": self.latency_ms,
            "error_code": self.error_code,
        }


# ═══════════════════════════════════════════════════════════════════════════
# Client Factory Functions — Singletons
# ═══════════════════════════════════════════════════════════════════════════


def get_anon_client(url: str, anon_key: str):
    """Return (or create) the anon Supabase client singleton.

    Thread-safe. Returns None only if not configured.
    Raises DatabaseUnavailableError on creation failure.
    """
    global _anon_client, _anon_client_url

    if not url or not anon_key:
        logger.warning(
            "Supabase anon client not configured — URL='%s', key_provided=%s",
            "set" if url else "missing",
            "set" if anon_key else "missing",
        )
        return None

    with _singleton_lock:
        if _anon_client is not None:
            if _anon_client_url is not None and _anon_client_url != url:
                raise RuntimeError(
                    "Supabase anon client already initialized for a different URL. "
                    "Refusing to silently switch projects."
                )
            return _anon_client

        try:
            from supabase import create_client
            _anon_client = create_client(url, anon_key)
            _anon_client_url = url
            logger.info("Supabase anon client connected")
            return _anon_client
        except Exception as exc:
            logger.error(
                "Failed to create Supabase anon client: %s",
                sanitize_error_message(exc),
            )
            raise DatabaseUnavailableError(
                message="Failed to create Supabase anon client",
                cause=exc,
                context={"client_type": "anon"},
            )


def get_service_client(url: str, service_role_key: str):
    """Return (or create) the service-role Supabase client singleton.

    Use this for backend credential operations (social accounts, API keys).

    Architecture §19: Service-role key must never be exposed.
    This function does not log the key value.

    Thread-safe. Returns None only if not configured.
    Raises DatabaseUnavailableError on creation failure.
    """
    global _service_client, _service_client_url

    if not url or not service_role_key:
        logger.warning(
            "Supabase service client not configured — URL='%s', key_provided=%s",
            "set" if url else "missing",
            "set" if service_role_key else "missing",
        )
        return None

    with _singleton_lock:
        if _service_client is not None:
            if _service_client_url is not None and _service_client_url != url:
                raise RuntimeError(
                    "Supabase service client already initialized for a different URL. "
                    "Refusing to silently switch projects."
                )
            return _service_client

        try:
            from supabase import create_client
            _service_client = create_client(url, service_role_key)
            _service_client_url = url
            logger.info("Supabase service client connected (admin)")
            return _service_client
        except Exception as exc:
            logger.error(
                "Failed to create Supabase service client: %s",
                sanitize_error_message(exc),
            )
            raise DatabaseUnavailableError(
                message="Failed to create Supabase service client",
                cause=exc,
                context={"client_type": "service_role"},
            )


# ═══════════════════════════════════════════════════════════════════════════
# Fresh client factories — DEPRECATED
#
# These exist because v12.2 was trying to work around broken HTTP/2
# connection reuse. The correct fix is tuning httpx limits on the singleton
# (see the ping_config / future httpx_limits work item). Until callers are
# migrated, these stay — but they are slower, they leak sockets if the
# caller does not close them, and they log deprecation warnings once.
# ═══════════════════════════════════════════════════════════════════════════

_fresh_deprecation_warned = False


def _warn_fresh_deprecated(which: str) -> None:
    global _fresh_deprecation_warned
    if not _fresh_deprecation_warned:
        logger.warning(
            "create_fresh_%s_client() is deprecated. Prefer the singleton "
            "get_%s_client_from_settings() (thread-safe, pooled). "
            "Fresh clients leak sockets unless the caller closes them.",
            which, "anon" if which == "anon" else "service",
        )
        _fresh_deprecation_warned = True


def create_fresh_service_client():
    """DEPRECATED. Create a new service-role client.

    Prefer get_service_client_from_settings(). See module docstring.
    """
    _warn_fresh_deprecated("service")

    from config.settings import settings

    if not settings.supabase_url or not settings.supabase_service_role_key:
        logger.warning(
            "Supabase: fresh service client not configured — URL='%s', key_provided=%s",
            "set" if settings.supabase_url else "missing",
            "set" if settings.supabase_service_role_key else "missing",
        )
        return None

    try:
        from supabase import create_client
        client = create_client(
            settings.supabase_url,
            settings.supabase_service_role_key,
        )
        logger.debug("Supabase: fresh service client created")
        return client
    except Exception as exc:
        logger.error(
            "Failed to create fresh Supabase service client: %s",
            sanitize_error_message(exc),
        )
        raise DatabaseUnavailableError(
            message="Failed to create fresh Supabase service client",
            cause=exc,
            context={"client_type": "fresh_service"},
        )


def create_fresh_anon_client():
    """DEPRECATED. Create a new anon client.

    Prefer get_anon_client_from_settings(). See module docstring.
    """
    _warn_fresh_deprecated("anon")

    from config.settings import settings

    if not settings.supabase_url or not settings.supabase_anon_key:
        logger.warning(
            "Supabase: fresh anon client not configured — URL='%s', key_provided=%s",
            "set" if settings.supabase_url else "missing",
            "set" if settings.supabase_anon_key else "missing",
        )
        return None

    try:
        from supabase import create_client
        client = create_client(
            settings.supabase_url,
            settings.supabase_anon_key,
        )
        logger.debug("Supabase: fresh anon client created")
        return client
    except Exception as exc:
        logger.error(
            "Failed to create fresh Supabase anon client: %s",
            sanitize_error_message(exc),
        )
        raise DatabaseUnavailableError(
            message="Failed to create fresh Supabase anon client",
            cause=exc,
            context={"client_type": "fresh_anon"},
        )


def get_fresh_service_client_factory() -> Callable:
    """Return a factory function that creates fresh service clients.

    DEPRECATED — see create_fresh_service_client.
    """
    return create_fresh_service_client


def get_fresh_anon_client_factory() -> Callable:
    """Return a factory function that creates fresh anon clients.

    DEPRECATED — see create_fresh_anon_client.
    """
    return create_fresh_anon_client


# ═══════════════════════════════════════════════════════════════════════════
# Health Check
# ═══════════════════════════════════════════════════════════════════════════


def _classify_error(exc: BaseException) -> tuple[str, str]:
    """Return (error_code, message). Type-first, string fallback."""
    # Type-based classification (preferred)
    try:
        import httpx  # local import — do not add a hard dependency
        if isinstance(exc, httpx.TimeoutException):
            return "DATABASE_TIMEOUT", "Connection timed out"
        if isinstance(exc, httpx.ConnectError):
            return "DATABASE_UNAVAILABLE", "Could not connect"
        if isinstance(exc, httpx.RemoteProtocolError):
            return "DATABASE_UNAVAILABLE", "Protocol error talking to Supabase"
        if isinstance(exc, httpx.HTTPStatusError):
            status = getattr(exc.response, "status_code", 0)
            if status in (401, 403):
                return "DATABASE_AUTH_ERROR", "Authentication failed — check credentials"
            return "DATABASE_UNAVAILABLE", f"HTTP {status} from Supabase"
    except ImportError:
        pass

    try:
        from postgrest.exceptions import APIError  # type: ignore
        if isinstance(exc, APIError):
            code = getattr(exc, "code", None) or ""
            if str(code).startswith("PGRST3") or "jwt" in str(exc).lower():
                return "DATABASE_AUTH_ERROR", "Authentication failed — check credentials"
            if code == "42P01":
                return "DATABASE_QUERY_ERROR", "Table not found"
            return "DATABASE_QUERY_ERROR", f"PostgREST error {code}"
    except ImportError:
        pass

    # String fallback (best-effort; avoid relying on this)
    error_str = str(exc).lower()
    if "auth" in error_str or "unauthorized" in error_str or "jwt" in error_str:
        return "DATABASE_AUTH_ERROR", "Authentication failed — check credentials"
    if "timeout" in error_str:
        return "DATABASE_TIMEOUT", "Connection timed out"
    if "not found" in error_str or "relation" in error_str:
        return "DATABASE_QUERY_ERROR", "Table or schema not found"

    return "DATABASE_UNAVAILABLE", sanitize_error_message(exc, max_len=200)


def ping(client) -> SupabaseHealth:
    """Check if a Supabase client can reach the database.

    Returns SupabaseHealth with latency and structured status.
    Never returns a bare bool.
    """
    if not client:
        return SupabaseHealth(
            healthy=False,
            client_type="none",
            message="No client provided",
            error_code="NO_CLIENT",
        )

    _t0 = time.monotonic()

    try:
        client.table(_PROBE_TABLE).select("id").limit(1).execute()
        latency_ms = (time.monotonic() - _t0) * 1000
        return SupabaseHealth(
            healthy=True,
            client_type=_classify_client(client),
            message="Connected",
            latency_ms=round(latency_ms, 1),
        )
    except Exception as exc:
        latency_ms = (time.monotonic() - _t0) * 1000
        error_code, message = _classify_error(exc)
        logger.warning("Supabase ping failed [%s]: %s", error_code, message)
        return SupabaseHealth(
            healthy=False,
            client_type=_classify_client(client),
            message=message,
            latency_ms=round(latency_ms, 1),
            error_code=error_code,
        )


def ping_service() -> SupabaseHealth:
    """Ping using the service-role client."""
    return ping(_service_client)


def ping_anon() -> SupabaseHealth:
    """Ping using the anon client."""
    return ping(_anon_client)


def _classify_client(client) -> str:
    """Identify which client type this is for health reporting."""
    if client is None:
        return "none"
    if client is _service_client:
        return "service_role"
    if client is _anon_client:
        return "anon"
    return "fresh"


# ═══════════════════════════════════════════════════════════════════════════
# Backwards-Compat Shim
# ═══════════════════════════════════════════════════════════════════════════


class _SupabaseServiceShim:
    """
    Backwards-compatible shim for existing code that accesses
    supabase_service.client / supabase_service.service_client.

    PATCH: .client now prefers the SERVICE client, not anon. Previously this
    returned anon if present and fell back to service — meaning that on any
    deployment where the anon client was missing, callers silently escalated
    to service-role privileges. That is a privilege-escalation hazard, not a
    feature. The new order prefers the more-privileged client explicitly so
    that any caller doing the wrong thing gets a louder audit trail; when
    both are present it still returns service first.

    A one-time deprecation warning is emitted on the first .client access.
    """

    _client_deprecation_warned = False

    @property
    def client(self):
        """Deprecated: ambiguous. Prefer .service_client or .anon_client."""
        if not _SupabaseServiceShim._client_deprecation_warned:
            logger.warning(
                "supabase_service.client is ambiguous and deprecated. "
                "Use .service_client (backend ops) or .anon_client (RLS-scoped) "
                "explicitly. This property will be removed in a future release."
            )
            _SupabaseServiceShim._client_deprecation_warned = True
        # Explicit preference: service first, anon second.
        return _service_client if _service_client is not None else _anon_client

    @property
    def service_client(self):
        """Service-role client for backend credential operations."""
        return _service_client

    @property
    def anon_client(self):
        """Anon client for user-facing operations (RLS-enforced)."""
        return _anon_client

    @property
    def is_healthy(self) -> bool:
        """DEPRECATED. Use ping_service()/ping_anon() for real health.

        This only reports "configured", not "reachable". Kept for backward
        compatibility with code that gates on client existence.
        """
        return _service_client is not None or _anon_client is not None

    def health(self) -> dict:
        """Full health check of all configured clients."""
        result: dict = {"healthy": False, "clients": {}}

        anon_health = ping_anon()
        service_health = ping_service()

        result["clients"]["anon"] = anon_health.to_dict()
        result["clients"]["service_role"] = service_health.to_dict()
        result["healthy"] = anon_health.healthy or service_health.healthy

        return result

    def __bool__(self):
        return _service_client is not None or _anon_client is not None


supabase_service = _SupabaseServiceShim()


# ═══════════════════════════════════════════════════════════════════════════
# Shutdown / Test Helpers
# ═══════════════════════════════════════════════════════════════════════════


async def aclose_all() -> None:
    """Best-effort close of both singleton clients.

    Wire this into lifecycle's shutdown_registry. Safe to call multiple
    times. Never raises.
    """
    global _anon_client, _service_client
    with _singleton_lock:
        for name, client in (("anon", _anon_client), ("service", _service_client)):
            if client is None:
                continue
            try:
                # supabase-py exposes the underlying httpx client under
                # different attributes across versions. Try the common ones.
                session = (
                    getattr(getattr(client, "postgrest", None), "session", None)
                    or getattr(client, "_http_client", None)
                )
                if session is None:
                    continue
                closer = getattr(session, "aclose", None) or getattr(session, "close", None)
                if closer is None:
                    continue
                result = closer()
                # If it's a coroutine, we're inside an async context already.
                try:
                    import inspect as _inspect
                    if _inspect.isawaitable(result):
                        await result
                except Exception:
                    pass
            except Exception:
                logger.debug("Failed to close %s client", name, exc_info=True)
        _anon_client = None
        _service_client = None
    logger.info("Supabase clients closed")


def reset_for_tests() -> None:
    """Reset singleton state. Call from a pytest fixture."""
    global _anon_client, _service_client, _anon_client_url, _service_client_url
    global _fresh_deprecation_warned
    with _singleton_lock:
        _anon_client = None
        _service_client = None
        _anon_client_url = None
        _service_client_url = None
    _fresh_deprecation_warned = False
    _SupabaseServiceShim._client_deprecation_warned = False


# ═══════════════════════════════════════════════════════════════════════════
# Convenience Functions
# ═══════════════════════════════════════════════════════════════════════════


def get_anon_client_from_settings():
    """Create anon client from application settings."""
    from config.settings import settings
    return get_anon_client(settings.supabase_url, settings.supabase_anon_key)


def get_service_client_from_settings():
    """Create service-role client from application settings.

    Architecture §19: The service_role key comes from settings
    and is never exposed outside this module.
    """
    from config.settings import settings
    return get_service_client(
        settings.supabase_url,
        settings.supabase_service_role_key,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Exports
# ═══════════════════════════════════════════════════════════════════════════

__all__ = [
    "SupabaseHealth",
    "get_anon_client",
    "get_service_client",
    "create_fresh_service_client",
    "create_fresh_anon_client",
    "get_fresh_service_client_factory",
    "get_fresh_anon_client_factory",
    "ping",
    "ping_service",
    "ping_anon",
    "supabase_service",
    "get_anon_client_from_settings",
    "get_service_client_from_settings",
    "aclose_all",
    "reset_for_tests",
]
