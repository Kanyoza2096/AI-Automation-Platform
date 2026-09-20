"""
Bridge: trend.publish.requested → DecisionGate → Action Kernel.

Keeps Trend Intelligence decoupled from social connectors.
The Action Kernel owns durable intent, approval, execution, and the hand-off
to the canonical publishing pipeline.
Never imports non-existent DirectorService.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Dict, Optional

logger = logging.getLogger("kanyoza.services.trend_publish_bridge")


async def handle_trend_publish_requested(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Accept a trend publish request and enqueue durable outbox work.

    Flow:
      trend.publish.requested
        → issue/attach decision
        → ActionKernel.enqueue(publish_content)
        → Action Outbox / governed worker
        → canonical publishing pipeline
    """
    job_id = payload.get("job_id") or f"trend_{uuid.uuid4().hex[:12]}"
    brand_id = payload.get("brand_id")
    workspace_id = str(payload.get("workspace_id") or "").strip()
    if not workspace_id:
        return {"ok": False, "job_id": job_id, "error": "workspace_required"}
    decision = payload.get("decision") or "publish"
    topic = str(payload.get("topic") or "")
    content = str(payload.get("content") or payload.get("caption") or "")
    media_url = payload.get("media_url")
    platform = payload.get("platform") or "facebook"
    dry_run = decision in ("schedule", "dry_run", "hold")

    logger.info(
        "Trend publish bridge: job=%s brand=%s decision=%s workspace=%s topic=%s",
        job_id,
        brand_id,
        decision,
        workspace_id,
        topic[:60],
    )

    # 1) Mark job accepted (best-effort)
    try:
        from services.supabase import get_service_client_from_settings
        client = get_service_client_from_settings()
        if client and job_id:
            try:
                client.table("trend_publish_jobs").update(
                    {"status": "accepted_by_bridge"}
                ).eq("job_id", job_id).execute()
            except Exception:
                pass
    except Exception as exc:
        logger.debug("supabase optional: %s", exc)

    container = None
    try:
        from service_container import get_container
        container = get_container()
    except Exception:
        container = None

    # 2) Ensure a DecisionGate id exists (explicit issue — not forgeable override)
    decision_id = payload.get("decision_id")
    if not decision_id:
        try:
            from core.decision_gate import decision_gate, DecisionSource
            issued = decision_gate.issue(
                source=DecisionSource.DIRECTOR_AUTO,
                workspace_id=str(workspace_id),
                action_name="publish",
                approved=not dry_run,
                reason=f"trend_bridge:{job_id}",
                risk_level="low",
                metadata={
                    "job_id": job_id,
                    "brand_id": brand_id,
                    "source": "trend_intelligence",
                    "dry_run": dry_run,
                },
            )
            decision_id = issued.decision_id
        except Exception as exc:
            logger.error("Trend bridge decision issue failed: %s", exc)
            return {"ok": False, "job_id": job_id, "error": f"decision_failed:{exc}"}

    # 3) Governed Action Kernel admission. Trend intelligence is a proposer,
    # never an execution authority. The Action Kernel owns durable intent,
    # approval, idempotency, execution and the existing canonical publish plane.
    try:
        from services.action_kernel import build_default_action_kernel
        db = None
        if container is not None:
            supabase = getattr(container, "supabase_service", None)
            db = getattr(supabase, "client", None)
        if db is None:
            try:
                from services.supabase import supabase_service
                db = supabase_service.service_client or supabase_service.client
            except Exception:
                db = None
        if db is None:
            return {
                "ok": False,
                "job_id": job_id,
                "error": "durable_action_kernel_database_required",
                "decision_id": decision_id,
            }

        kernel = build_default_action_kernel(db=db, allow_in_memory=False)
        result = await kernel.enqueue(
            workspace_id=str(workspace_id),
            action_type="publish_content",
            parameters={
                "brand_id": brand_id,
                "platform": str(platform),
                "topic": topic,
                "caption": content,
                "decision_id": str(decision_id),
                "scheduled_post_id": payload.get("scheduled_post_id"),
                "image_url": media_url,
            },
            risk_level="high",
            decision_id=str(decision_id),
            correlation_id=str(payload.get("correlation_id") or job_id),
            idempotency_key=str(payload.get("idempotency_key") or f"trend-publish:{job_id}"),
            source="trend_intelligence",
            approved=bool(payload.get("approved", False)),
        )
        logger.info(
            "Trend publish admitted to Action Kernel job=%s status=%s intent=%s",
            job_id, result.get("status"), result.get("intent_id"),
        )
        return {
            "ok": True,
            "job_id": job_id,
            "status": result.get("status"),
            "intent_id": result.get("intent_id"),
            "decision_id": decision_id,
            "accepted": bool(result.get("accepted")),
            "governed": True,
            "execution_requested": False,
        }
    except Exception as exc:
        logger.exception("Trend publish Action Kernel admission failed: %s", exc)
        return {
            "ok": False,
            "job_id": job_id,
            "error": str(exc),
            "decision_id": decision_id,
        }


async def wire_trend_publish_bridge(event_bus) -> bool:
    """Subscribe handler to trend.publish.requested on the given bus."""
    if event_bus is None:
        return False

    async def _on_event(event_name: str = "", payload: Dict[str, Any] | None = None, **kwargs):
        data = payload if isinstance(payload, dict) else kwargs.get("data") or kwargs
        if not isinstance(data, dict):
            data = {}
        try:
            await handle_trend_publish_requested(data)
        except Exception as exc:
            logger.exception("trend.publish.requested handler error: %s", exc)

    # Support various bus subscribe APIs
    for method_name in ("subscribe", "on", "add_listener"):
        method = getattr(event_bus, method_name, None)
        if not callable(method):
            continue
        try:
            result = method("trend.publish.requested", _on_event)
            # ✅ FIXED: Await if it returns a coroutine
            if asyncio.iscoroutine(result):
                await result
            logger.info("Wired trend.publish.requested → publish bridge via %s", method_name)
            return True
        except TypeError:
            try:
                result = method("trend.publish.requested", handler=_on_event)
                # ✅ FIXED: Await if it returns a coroutine
                if asyncio.iscoroutine(result):
                    await result
                logger.info("Wired trend.publish.requested via %s(handler=)", method_name)
                return True
            except Exception:
                continue
        except Exception as exc:
            logger.debug("wire via %s failed: %s", method_name, exc)

    logger.warning("Could not wire trend.publish.requested — no compatible subscribe API")
    return False
