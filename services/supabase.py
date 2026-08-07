"""
Supabase client factory — v12.1.

Provides separate anon and service-role clients.
SocialAccountRepository and other backend operations should use
the service-role client for credential lookups.

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

import logging
import os
from dataclasses import dataclass
from typing import Optional

from core.errors import (
    DatabaseUnavailableError,
    DatabaseAuthError,
    ErrorCode,
)

logger = logging.getLogger("kanyoza.services.supabase")

_anon_client = None
_service_client = None


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
    client_type: str  # "anon", "service", "none"
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
# Client Factory Functions
# ═══════════════════════════════════════════════════════════════════════════


def get_anon_client(url: str, anon_key: str):
    """Return (or create) the anon Supabase client singleton.
    
    Returns None only if not configured (intentional).
    Does NOT return None for connection failures — those raise.
    """
    global _anon_client
    if _anon_client is not None:
        return _anon_client
    
    if not url or not anon_key:
        logger.warning(
            "Supabase anon client not configured — URL='%s', key_provided=%s",
            "set" if url else "missing",
            "set" if anon_key else "missing",
        )
        return None
    
    try:
        from supabase import create_client
        _anon_client = create_client(url, anon_key)
        logger.info("Supabase anon client connected: %s", url)
        return _anon_client
    except Exception as exc:
        logger.error("Failed to create Supabase anon client: %s", exc)
        raise DatabaseUnavailableError(
            message=f"Failed to create Supabase anon client: {url}",
            cause=exc,
            context={"url": url, "client_type": "anon"},
        )


def get_service_client(url: str, service_role_key: str):
    """Return (or create) the service-role Supabase client singleton.
    
    Use this for backend credential operations (social accounts, API keys).
    
    Architecture §19: Service-role key must never be exposed.
    This function does not log the key value.
    
    Returns None only if not configured.
    Raises DatabaseUnavailableError on connection failure.
    """
    global _service_client
    if _service_client is not None:
        return _service_client
    
    if not url or not service_role_key:
        logger.warning(
            "Supabase service client not configured — URL='%s', service_key_provided=%s",
            "set" if url else "missing",
            "set" if service_role_key else "missing",
        )
        return None
    
    try:
        from supabase import create_client
        _service_client = create_client(url, service_role_key)
        logger.info("Supabase service client connected (admin): %s", url)
        return _service_client
    except Exception as exc:
        logger.error("Failed to create Supabase service client: %s", exc)
        raise DatabaseUnavailableError(
            message=f"Failed to create Supabase service client: {url}",
            cause=exc,
            context={"url": url, "client_type": "service_role"},
        )


# ═══════════════════════════════════════════════════════════════════════════
# Health Check
# ═══════════════════════════════════════════════════════════════════════════


def ping(client) -> SupabaseHealth:
    """Check if a Supabase client can reach the database.
    
    Returns SupabaseHealth with latency and structured status.
    Never returns a bare bool.
    
    Architecture §33 — Health Checks:
      Differentiate: healthy, degraded, unavailable.
    """
    if not client:
        return SupabaseHealth(
            healthy=False,
            client_type="none",
            message="No client provided",
            error_code="NO_CLIENT",
        )
    
    import time
    _t0 = time.monotonic()
    
    try:
        # Lightweight query — just check connectivity
        client.table("bot_events").select("id").limit(1).execute()
        latency_ms = (time.monotonic() - _t0) * 1000
        return SupabaseHealth(
            healthy=True,
            client_type=_classify_client(client),
            message="Connected",
            latency_ms=round(latency_ms, 1),
        )
    except Exception as exc:
        latency_ms = (time.monotonic() - _t0) * 1000
        error_str = str(exc).lower()
        
        # Classify the type of failure
        if "auth" in error_str or "unauthorized" in error_str or "jwt" in error_str:
            error_code = "DATABASE_AUTH_ERROR"
            message = "Authentication failed — check credentials"
        elif "timeout" in error_str or "connect" in error_str:
            error_code = "DATABASE_TIMEOUT"
            message = "Connection timed out"
        elif "not found" in error_str or "relation" in error_str:
            error_code = "DATABASE_QUERY_ERROR"
            message = "Table or schema not found"
        else:
            error_code = "DATABASE_UNAVAILABLE"
            message = str(exc)[:200]
        
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
    if client is _service_client:
        return "service_role"
    elif client is _anon_client:
        return "anon"
    return "unknown"


# ═══════════════════════════════════════════════════════════════════════════
# Backwards-Compat Shim
# ═══════════════════════════════════════════════════════════════════════════


class _SupabaseServiceShim:
    """
    Backwards-compatible shim for existing code that accesses
    supabase_service.client / supabase_service.service_client.
    
    Prefers service_role client for backend operations.
    """
    
    @property
    def client(self):
        """General client — prefers anon for frontend, but returns service if available."""
        return _anon_client if _anon_client is not None else _service_client

    @property
    def service_client(self):
        """Service-role client for backend credential operations.
        
        Architecture §13: Use this for server-side operations
        like social account lookups, not for user-facing queries.
        """
        return _service_client

    @property
    def anon_client(self):
        """Anon client for user-facing operations (RLS-enforced)."""
        return _anon_client

    @property
    def is_healthy(self) -> bool:
        """Quick health check — True if at least one client is connected."""
        return self.client is not None

    def health(self) -> dict:
        """Full health check of all configured clients."""
        result = {
            "healthy": False,
            "clients": {},
        }
        
        anon_health = ping_anon()
        service_health = ping_service()
        
        result["clients"]["anon"] = anon_health.to_dict()
        result["clients"]["service_role"] = service_health.to_dict()
        result["healthy"] = anon_health.healthy or service_health.healthy
        
        return result

    def __bool__(self):
        return self.client is not None


supabase_service = _SupabaseServiceShim()


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
