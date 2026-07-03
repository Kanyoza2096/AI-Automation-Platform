"""
Supabase client factory.

FIX applied: removed all .query() calls (that method does not exist in
supabase-py). The client is a thin wrapper around the PostgREST table API
(.table().select().execute(), .insert(), .upsert(), etc.).

Schema initialization is done via the Supabase dashboard or SQL migrations —
NOT in application code.
"""

import logging
from typing import Optional

logger = logging.getLogger("kanyoza.services.supabase")

_anon_client = None
_service_client = None


def get_anon_client(url: str, anon_key: str):
    """Return (or create) the anon Supabase client singleton."""
    global _anon_client
    if _anon_client is not None:
        return _anon_client
    if not url or not anon_key:
        logger.warning("Supabase anon client not configured — persistence disabled")
        return None
    try:
        from supabase import create_client
        _anon_client = create_client(url, anon_key)
        logger.info("Supabase anon client connected: %s", url)
        return _anon_client
    except Exception as exc:
        logger.error("Failed to create Supabase anon client: %s", exc)
        return None


def get_service_client(url: str, service_role_key: str):
    """Return (or create) the service-role Supabase client singleton."""
    global _service_client
    if _service_client is not None:
        return _service_client
    if not url or not service_role_key:
        logger.warning("Supabase service client not configured — admin ops disabled")
        return None
    try:
        from supabase import create_client
        _service_client = create_client(url, service_role_key)
        logger.info("Supabase service client connected (admin): %s", url)
        return _service_client
    except Exception as exc:
        logger.error("Failed to create Supabase service client: %s", exc)
        return None


def ping(client) -> bool:
    """Return True if the client can reach Supabase."""
    if not client:
        return False
    try:
        client.table("bot_events").select("id").limit(1).execute()
        return True
    except Exception as exc:
        logger.debug("Supabase ping failed: %s", exc)
        return False


class _SupabaseServiceShim:
    """
    Backwards-compat shim so routes can do:
        from services.supabase import supabase_service
        if supabase_service and supabase_service.client:
            supabase_service.client.table(...).execute()
    This maps .client to whichever singleton client has been initialised.
    Returns None (falsy) when Supabase is not configured.
    """
    @property
    def client(self):
        return _service_client if _service_client is not None else _anon_client

    def __bool__(self):
        return self.client is not None


supabase_service = _SupabaseServiceShim()


# ---------------------------------------------------------------------------
# Zero-arg convenience accessors — read credentials from Settings so new
# modules (repositories, auth) don't need to thread URLs through every call.
# ---------------------------------------------------------------------------

def get_anon_client_from_settings():
    """Return the anon client using credentials from Settings. Returns None
    when Supabase is not configured (graceful degradation)."""
    from config.settings import settings
    return get_anon_client(settings.supabase_url, settings.supabase_anon_key)


def get_service_client_from_settings():
    """Return the service-role client using credentials from Settings.
    Returns None when Supabase is not configured (graceful degradation)."""
    from config.settings import settings
    return get_service_client(settings.supabase_url, settings.supabase_service_role_key)
