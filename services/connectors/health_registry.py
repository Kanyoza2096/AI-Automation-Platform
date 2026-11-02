"""Integration health registry.

Provider-specific health probes live here instead of in platform transport code.
The registry returns a stable, provider-neutral status shape so Mission Control
and realtime transports do not need to know how a provider is configured.

V1.1 (2026-11-02) — explicit 3-state status field:

  Previously every probe returned only {ok: True/False}. The dashboard
  counted all non-ok results as "degraded", which conflated two very
  different cases:
    - credentials absent (never wired up — unconfigured)
    - credentials present but health check failed (degraded)

  Every probe now returns a `status` field with one of:
    - "healthy"       — credentials present, check passed
    - "degraded"      — credentials present, check failed
    - "unconfigured"  — credentials absent (check cannot run)

  The registry preserves `status` in integration_status() so dashboards
  can distinguish "needs setup" from "needs attention".

  Note: current probes are credential-presence checks, not active
  health checks. None of them can return "degraded" yet — every
  ok=False path is unconfigured. The `degraded` state exists for
  future probes that actually test connectivity.
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
            return {
                "ok": True,
                "platform": "facebook",
                "status": "healthy",
                "mode": "global",
            }
        try:
            from repositories.social_account_repository import SocialAccountRepository
            accounts = SocialAccountRepository().find_by_platform("facebook")
            enabled = [
                a for a in accounts
                if getattr(a, "enabled", False) and getattr(a, "access_token", None)
            ]
            if enabled:
                return {
                    "ok": True,
                    "platform": "facebook",
                    "status": "healthy",
                    "mode": "multi_tenant",
                    "accounts_configured": len(enabled),
                }
        except Exception as exc:
            logger.debug("Facebook multi-tenant health probe failed: %s", exc)
        # No global config, no enabled accounts → nothing to test → unconfigured.
        return {
            "ok": False,
            "platform": "facebook",
            "status": "unconfigured",
            "mode": "unconfigured",
        }
    except Exception as exc:
        # Facebook service import raised → the provider exists but is broken.
        return {
            "ok": False,
            "platform": "facebook",
            "status": "degraded",
            "error": str(exc)[:100],
        }


def _credential_platform(platform: str, setting: str) -> HealthProbe:
    def probe() -> dict:
        try:
            from config.settings import settings
            has_credential = bool(getattr(settings, setting, ""))
            if has_credential:
                return {
                    "ok": True,
                    "platform": platform,
                    "status": "healthy",
                }
            # Credential absent → not wired up → unconfigured, not degraded.
            return {
                "ok": False,
                "platform": platform,
                "status": "unconfigured",
                "error": f"missing credential: settings.{setting}",
            }
        except Exception as exc:
            # Settings load raised → cannot determine state → degraded.
            return {
                "ok": False,
                "platform": platform,
                "status": "degraded",
                "error": str(exc)[:100],
            }
    return probe


def _instagram() -> dict:
    try:
        from repositories.social_account_repository import SocialAccountRepository
        accounts = SocialAccountRepository().find_by_platform("instagram")
        enabled = [a for a in accounts if getattr(a, "enabled", False)]
        if enabled:
            return {
                "ok": True,
                "platform": "instagram",
                "status": "healthy",
                "accounts_configured": len(enabled),
            }
        # No enabled Instagram accounts → unconfigured.
        return {
            "ok": False,
            "platform": "instagram",
            "status": "unconfigured",
            "error": "no enabled instagram accounts",
        }
    except Exception as exc:
        # Repository query raised → broken backend → degraded.
        return {
            "ok": False,
            "platform": "instagram",
            "status": "degraded",
            "error": str(exc)[:100],
        }


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
        return {
            "ok": False,
            "platform": platform,
            "status": "unconfigured",
            "error": "No health probe registered",
        }
    result = probe()
    # Defensive: ensure status is always present
    if "status" not in result:
        result["status"] = "healthy" if result.get("ok") else "unconfigured"
    return result


def integration_status() -> list[dict]:
    """Return dashboard-ready health status without provider logic in callers.

    V1.1 — preserve the `status` and `error` fields from check().

    The `status` field distinguishes "healthy", "degraded", and
    "unconfigured". The `healthy` boolean is preserved for backward
    compatibility with any consumer that hasn't been updated to read
    `status` yet.
    """
    out = []
    for platform in _PROBES:
        result = check(platform)
        out.append({
            "platform": result["platform"],
            "healthy": result.get("ok", False),
            "status": result.get("status", "unconfigured"),
            "mode": result.get("mode"),
            "accounts_configured": result.get("accounts_configured"),
            "error": result.get("error"),
        })
    return out


__all__ = ["check", "integration_status"]
