"""Integration health registry.

Provider-specific health probes live here instead of in platform transport code.
The registry returns a stable, provider-neutral status shape so Mission Control
and realtime transports do not need to know how a provider is configured.
"""
from __future__ import annotations

import logging
from typing import Callable

logger = logging.getLogger("kanyoza.connectors.health")

HealthProbe = Callable[[], dict]


def _facebook() -> dict:
    try:
        from services.facebook import facebook as fb_svc
        if fb_svc.is_configured:
            return {"ok": True, "platform": "facebook", "mode": "global"}
        try:
            from repositories.social_account_repository import SocialAccountRepository
            accounts = SocialAccountRepository().find_by_platform("facebook")
            enabled = [a for a in accounts if getattr(a, "enabled", False) and getattr(a, "access_token", None)]
            if enabled:
                return {"ok": True, "platform": "facebook", "mode": "multi_tenant", "accounts_configured": len(enabled)}
        except Exception as exc:
            logger.debug("Facebook multi-tenant health probe failed: %s", exc)
        return {"ok": False, "platform": "facebook", "mode": "unconfigured"}
    except Exception as exc:
        return {"ok": False, "platform": "facebook", "error": str(exc)[:100]}


def _credential_platform(platform: str, setting: str) -> HealthProbe:
    def probe() -> dict:
        try:
            from config.settings import settings
            return {"ok": bool(getattr(settings, setting, "")), "platform": platform}
        except Exception:
            return {"ok": False, "platform": platform}
    return probe


def _instagram() -> dict:
    try:
        from repositories.social_account_repository import SocialAccountRepository
        accounts = SocialAccountRepository().find_by_platform("instagram")
        enabled = [a for a in accounts if getattr(a, "enabled", False)]
        if enabled:
            return {"ok": True, "platform": "instagram", "accounts_configured": len(enabled)}
        return {"ok": False, "platform": "instagram"}
    except Exception:
        return {"ok": False, "platform": "instagram"}


_PROBES: dict[str, HealthProbe] = {
    "facebook": _facebook,
    "twitter": _credential_platform("twitter", "twitter_access_token"),
    "linkedin": _credential_platform("linkedin", "linkedin_access_token"),
    "instagram": _instagram,
    "whatsapp": _credential_platform("whatsapp", "whatsapp_token"),
    "telegram": _credential_platform("telegram", "telegram_bot_token"),
}


def check(platform: str) -> dict:
    """Return a normalized health result for one registered integration."""
    probe = _PROBES.get(platform)
    if probe is None:
        return {"ok": False, "platform": platform, "error": "No health probe registered"}
    return probe()


def integration_status() -> list[dict]:
    """Return dashboard-ready health status without provider logic in callers."""
    return [
        {
            "platform": result["platform"],
            "healthy": result.get("ok", False),
            "mode": result.get("mode"),
            "accounts_configured": result.get("accounts_configured"),
        }
        for result in (check(platform) for platform in _PROBES)
    ]


__all__ = ["check", "integration_status"]
