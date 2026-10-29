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
    _from_outbox: bool = False, execution_managed: bool = False, **kwargs: Any,
) -> Dict[str, Any]:
    from domain.publishing.canonical_pipeline import run_canonical_publish
    return await run_canonical_publish(
        container=container, workspace_id=workspace_id, brand_id=brand_id,
        platform=platform, topic=topic or "", caption=caption or "",
        dry_run=dry_run, source=source, risk_level=risk_level,
        decision_id=decision_id, durable=durable, _from_outbox=_from_outbox, brand=brand,
        scheduled_post_id=scheduled_post_id, execution_managed=execution_managed, **kwargs)


async def ensure_social_plugins(container=None) -> dict:
    """Ensure social platform plugins are discoverable AND bound to the container.

    The canonical publish pipeline (`canonical_pipeline._connector_execute`)
    resolves live connectors from `container.social_plugins`. Importing the
    plugin packages makes them visible to the PluginRegistry, but does not
    bind them to the container. This function closes that gap so the publish
    pipeline can find and invoke the real connector instead of falling back
    to simulation.
    """
    report = {"ok": True, "plugins": [], "errors": [], "bound": []}

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
            # Preserve the legacy attachment for observability
            setattr(container, "social_plugins_report", report)

            # ── Bind the actual plugins dict to the container ─────────
            # This is what canonical_pipeline._connector_execute() reads.
            try:
                from plugins.base import get_plugin_registry
                registry = get_plugin_registry()
            except Exception as exc:
                report["errors"].append(f"registry: {exc}")
                registry = None

            social_plugins: Dict[str, Any] = {}
            if registry is not None:
                for name in (
                    "facebook", "twitter", "linkedin",
                    "instagram", "whatsapp", "telegram",
                ):
                    plugin = registry.get(name)
                    if plugin is not None:
                        social_plugins[name] = plugin

            setattr(container, "social_plugins", social_plugins)
            report["bound"] = list(social_plugins.keys())

            # Ensure industry_plugins exists (may be set elsewhere already)
            if not hasattr(container, "industry_plugins"):
                setattr(container, "industry_plugins", {})

        except Exception as exc:
            report["errors"].append(f"bind: {exc}")

    return report


async def execute_governed_publish(
    *,
    container=None,
    workspace_id: str,
    brand_id: Optional[str] = None,
    platform: Optional[str] = None,
    topic: str = "",
    caption: str = "",
    source: str = "unknown",
    risk_level: str = "low",
    decision_id: Optional[str] = None,
    approved: bool = False,
    idempotency_key: Optional[str] = None,
    correlation_id: Optional[str] = None,
    scheduled_post_id: Optional[str] = None,
    automation_job_id: Optional[str] = None,
    image_url: Optional[str] = None,
    image_bytes: Optional[bytes] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Execute publishing through the single governed Action Kernel boundary.

    The Action Kernel owns action identity, approval, the durable action intent,
    ExecutionLedger and the hand-off to the canonical publishing pipeline. The
    canonical pipeline remains the provider/governance execution plane; this
    helper deliberately does not create a second ledger or outbox.
    """
    from core.publish_authorization import assert_publish_allowed
    from services.action_kernel import build_default_action_kernel

    decision = assert_publish_allowed(decision_id, workspace_id, strict=True)
    if not bool(getattr(decision, "approved", False)):
        return {"ok": False, "status": "approval_required", "error": "publish decision is not approved"}

    db = getattr(getattr(container, "supabase_service", None), "client", None) if container else None
    if db is None:
        try:
            from services.supabase import supabase_service
            db = supabase_service.service_client or supabase_service.client
        except Exception:
            db = None
    if db is None:
        raise RuntimeError("durable action kernel database required")

    import base64
    parameters: Dict[str, Any] = {
        "brand_id": brand_id,
        "platform": platform or "facebook",
        "topic": topic,
        "caption": caption,
        "decision_id": str(getattr(decision, "decision_id", None) or decision_id),
        "scheduled_post_id": scheduled_post_id,
        "automation_job_id": automation_job_id,
        "image_url": image_url,
        "dry_run": bool(dry_run),
        "container": container,  
    }
    if image_bytes:
        parameters["image_bytes_b64"] = base64.b64encode(image_bytes).decode("ascii")

    kernel = build_default_action_kernel(db=db, allow_in_memory=False)
    return await kernel.execute(
        workspace_id=workspace_id,
        action_type="publish_content",
        parameters=parameters,
        risk_level="high",
        decision_id=parameters["decision_id"],
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
        source=source,
    )
