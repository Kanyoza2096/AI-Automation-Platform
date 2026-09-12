"""Canonical publish orchestration → domain.publishing.canonical_pipeline."""
from __future__ import annotations
from typing import Any, Dict, Optional

async def run_publish(
    *, container=None, workspace_id: str, brand_id: Optional[str] = None,
    platform: Optional[str] = None, topic: str = "", caption: str = "",
    brand=None,
    dry_run: bool = False, source: str = "api", risk_level: str = "unknown",
    decision_id: Optional[str] = None, durable: bool = True,
    scheduled_post_id: Optional[str] = None,
    _from_outbox: bool = False, **kwargs: Any,
) -> Dict[str, Any]:
    from domain.publishing.canonical_pipeline import run_canonical_publish
    return await run_canonical_publish(
        container=container, workspace_id=workspace_id, brand_id=brand_id,
        platform=platform, topic=topic or "", caption=caption or "",
        dry_run=dry_run, source=source, risk_level=risk_level,
        decision_id=decision_id, durable=durable, _from_outbox=_from_outbox, brand=brand,
        scheduled_post_id=scheduled_post_id, **kwargs)


async def ensure_social_plugins(container=None) -> dict:
    """Ensure social platform plugins are discoverable. Safe no-op on missing packages."""
    report = {"ok": True, "plugins": [], "errors": []}
    for name in ("facebook", "twitter", "linkedin", "integrations"):
        try:
            if name == "facebook":
                from plugins.social import facebook  # noqa: F401
            else:
                __import__(f"plugins.{name}", fromlist=["*"])
            report["plugins"].append(name)
        except Exception as exc:
            report["errors"].append(f"{name}: {exc}")
    if container is not None:
        try:
            setattr(container, "social_plugins_report", report)
        except Exception:
            pass
    return report
