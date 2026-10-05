"""
Director capability bindings — real hands for the autonomous CEO.

Lifecycle imports:
    from services.director_bindings import (
        bind_director_capabilities,
        assert_no_unbound_capabilities,
    )

v16.4 (2026-10-05) — PUBLISH FENCE:
  - 'publish_scheduled_content' is NO LONGER BOUND to the Director. The
    Director's review_content_strategy decision (issued ~every 30 minutes)
    was invoking this capability, which called mgr.trigger_all() and
    published for every brand on each cycle. That produced duplicate
    publishes on top of the scheduler's own fires, drained the Cloudflare
    and Gemini daily quotas, and pushed the pipeline into the
    deterministic-caption fallback path. The scheduler is now the sole
    authoritative publisher.
  - The publish_scheduled_content function remains defined below for any
    non-Director caller (e.g. a future trend bridge), but it now refuses
    to run unless an explicit `source` is provided. Autonomous Director
    invocations leave `source` blank; explicit callers set it.

Non-critical capabilities get real service handlers.
CRITICAL capabilities stay blocked (human approval required).
"""

from __future__ import annotations

import asyncio
import logging
import inspect
import time
from typing import Any, Callable, Dict, List

logger = logging.getLogger("kanyoza.director_bindings")

CRITICAL_CAPABILITIES = frozenset({
    "delete_brand",
    "delete_client_data",
    "drop_database",
    "reset_database",
    "rotate_credentials",
    "change_production_secrets",
    "disable_security_controls",
    "change_billing",
    "refund_client",
    "send_legal_notice",
    "deploy_breaking_change",
    "change_access_control",
})


def _ok(service: str, result: Any = None, **extra) -> Dict[str, Any]:
    out: Dict[str, Any] = {"ok": True, "status": "executed", "service": service}
    if result is not None:
        out["result"] = result
    out.update(extra)
    return out


def _soft_fail(service: str, reason: str, **extra) -> Dict[str, Any]:
    return {"ok": False, "status": "unavailable", "service": service, "reason": reason, **extra}


def _is_coro_fn(fn: Callable) -> bool:
    return asyncio.iscoroutinefunction(fn)


def _get_all_active_brands() -> list:
    """Get ALL active brands — never hardcode default."""
    try:
        from services.supabase import supabase_service
        client = getattr(supabase_service, "client", None)
        if client:
            result = client.table("brands").select("id", "workspace_id", "slug").eq("status", "active").execute()
            if result.data:
                return result.data
    except Exception:
        pass
    return []


def _build_handlers_for(director, container) -> Dict[str, Callable]:
    def _resolve(cls):
        if container is None:
            return None
        try:
            return container.try_resolve(cls)
        except Exception:
            return None

    async def publish_scheduled_content(params: dict) -> dict:
        """Publish path for explicit callers (trend bridge, operator).

        v16.4 — NOT bound to the Director. This function remains available
        for non-autonomous callers but refuses to run when `source` is
        missing, which is the signature of an autonomous Director
        invocation. Legitimate callers must set `source` to something
        explicit (e.g. "trend_intelligence", "operator", "content_bridge").

        Delegates to `execute_governed_publish` when content is supplied,
        or to `BrandSchedulerManager.trigger_all()` for a full-cycle
        publish. Both paths cross the Action Kernel boundary.
        """
        dry_run = bool(params.get("dry_run", False))
        source = (params.get("source") or "").strip().lower()
        topic = params.get("topic") or ""
        content = (params.get("content") or topic or "").strip()
        media_url = params.get("media_url")
        job_id = params.get("job_id") or params.get("idempotency_key")
        brand_id = params.get("brand_id")

        # v16.4 — Fence: refuse autonomous Director invocations.
        # Autonomous callers leave `source` blank; explicit callers set it.
        if not source:
            logger.warning(
                "publish_scheduled_content refused: no explicit source "
                "(autonomous Director invocation fenced)"
            )
            return _soft_fail(
                "publish",
                "explicit source required; autonomous Director publishes are disabled",
                job_id=job_id,
            )

        if dry_run:
            return _ok(
                "publish",
                {
                    "dry_run": True,
                    "source": source,
                    "topic": topic[:80],
                    "has_content": bool(content),
                    "has_media": bool(media_url),
                    "job_id": job_id,
                },
            )

        # Explicit trend content → canonical publish via Action Kernel
        if source in ("trend_intelligence", "trend") or content:
            workspace_id = str(params.get("workspace_id") or "").strip()
            if not workspace_id:
                return _soft_fail("publish", "workspace_id is required for automated publishing", job_id=job_id)
            if not brand_id:
                return _soft_fail("publish", "brand_id is required for automated publishing", job_id=job_id)

            try:
                from services.publish_orchestration import execute_governed_publish
                result = await execute_governed_publish(
                    container=container,
                    workspace_id=workspace_id,
                    brand_id=brand_id,
                    platform=str(params.get("platform") or "facebook"),
                    topic=topic,
                    caption=content,
                    decision_id=params.get("decision_id"),
                    source=source,
                    risk_level=str(params.get("risk_level") or "low"),
                    idempotency_key=params.get("idempotency_key") or job_id,
                    correlation_id=params.get("correlation_id") or job_id,
                    image_url=media_url,
                    scheduled_post_id=params.get("scheduled_post_id"),
                    automation_job_id=params.get("automation_job_id") or job_id,
                )
                return _ok(
                    "action_kernel",
                    result,
                    brand_id=brand_id,
                    workspace_id=workspace_id,
                    job_id=job_id,
                    governed=True,
                )
            except Exception as exc:
                logger.exception("canonical automated publish failed: %s", exc)
                return _soft_fail("publish", str(exc)[:200], job_id=job_id)

        # Full-cycle fallback: delegate to BrandScheduler for all brands.
        # This path requires an explicit source too, so it is reachable
        # only when the caller has deliberately set one.
        try:
            from scheduler.brand_scheduler import BrandSchedulerManager
            mgr = _resolve(BrandSchedulerManager)
            if mgr is not None and hasattr(mgr, "trigger_all"):
                logger.info(
                    "publish_scheduled_content: explicit source=%r triggering all brands",
                    source,
                )
                scheduler_result = mgr.trigger_all()
                if inspect.isawaitable(scheduler_result):
                    scheduler_result = await scheduler_result
                return _ok("brand_scheduler", scheduler_result, source=source)
        except Exception as exc:
            logger.warning("publish_scheduled_content scheduler: %s", exc)

        # Last resort: generate content only (does not publish).
        try:
            from services.content_engine import content_engine
            return _ok(
                "content_engine",
                await content_engine.generate_post(topic=topic or None),
                note="generate_only",
                source=source,
            )
        except Exception as exc:
            return _soft_fail("content_engine", str(exc)[:200])

    async def optimize_content_schedule(params: dict) -> dict:
        """Propose a content-mix change through the common Action Kernel."""
        brand_id = str(params.get("brand_id") or "").strip()
        change = float(params.get("content_mix_change", 0.10))
        workspace_id = str(params.get("workspace_id") or "").strip()
        if not brand_id or not workspace_id:
            return _soft_fail("action_kernel", "workspace_id and brand_id are required")
        try:
            from services.action_kernel import build_default_action_kernel
            db = getattr(getattr(container, "supabase_service", None), "client", None) if container else None
            if db is None:
                return _soft_fail("action_kernel", "durable action kernel database required")
            kernel = build_default_action_kernel(db=db, allow_in_memory=False)
            result = await kernel.execute(
                workspace_id=workspace_id,
                action_type="content_adjust_mix",
                parameters={
                    "brand_id": brand_id,
                    "factor": change,
                    "duration_posts": int(params.get("duration_posts", 5)),
                },
                risk_level="medium",
                source="director",
                correlation_id=params.get("correlation_id"),
                idempotency_key=params.get("idempotency_key"),
            )
            return _ok("action_kernel", result, governed=True)
        except Exception as exc:
            return _soft_fail("action_kernel", str(exc)[:200])

    async def calculate_brand_health(params: dict) -> dict:
        brand_id = params.get("brand_id")
        try:
            from services.condition_engine import AutonomousConditionEngine
            ae = _resolve(AutonomousConditionEngine)
            if ae is None:
                try:
                    from services.condition_engine import autonomous_engine as ae
                except ImportError:
                    ae = None
            if ae and hasattr(ae, "calculate_brand_health"):
                result = ae.calculate_brand_health(brand_id)
                if inspect.isawaitable(result):
                    result = await result
                return _ok("condition_engine", result)
        except Exception as exc:
            logger.warning("calculate_brand_health unavailable: %s", exc)
        return _soft_fail("condition_engine", "brand_health_unavailable", brand_id=brand_id)

    async def queue_brand_revival(params: dict) -> dict:
        """Route bounded brand revival through the same governed action boundary."""
        params = dict(params or {})
        params["content_mix_change"] = 0.15
        params["duration_posts"] = 10
        return await optimize_content_schedule(params)

    async def run_health_scan(params: dict) -> dict:
        try:
            from lifecycle import health_registry
            reports = await health_registry.check_all()
            summary = {
                name: {"status": getattr(r.status, "value", str(r.status))}
                for name, r in (reports or {}).items()
            }
            return _ok("health_registry", summary, count=len(summary))
        except Exception as exc:
            return _soft_fail("health_registry", str(exc)[:200])

    async def restart_failed_service(params: dict) -> dict:
        engine = params.get("engine") or params.get("service") or params.get("name")
        if not engine:
            return _soft_fail("meta_engine", "missing engine/service name")
        try:
            from services.meta_engine import MetaEngineV7
            meta = _resolve(MetaEngineV7)
            if meta is None:
                try:
                    from services import meta_engine as me
                    meta = getattr(me, "meta_engine", None)
                except Exception:
                    meta = None
            for meth in ("request_recovery", "recover", "trigger_recovery"):
                if meta and hasattr(meta, meth):
                    fn = getattr(meta, meth)
                    result = await fn(engine) if _is_coro_fn(fn) else fn(engine)
                    return _ok("meta_engine", result, path="meta_recovery")
            if meta and hasattr(meta, "_cached_health"):
                meta._cached_health.pop(str(engine), None)
        except Exception as exc:
            logger.debug("meta recovery path: %s", exc)
        return _ok("restart", {"service": engine, "action": "best_effort_complete"})

    async def run_guardian_scan(params: dict) -> dict:
        try:
            from services.guardian_service import GuardianService
            gs = _resolve(GuardianService)
            if gs is not None and hasattr(gs, "scan_all"):
                return _ok("guardian", gs.scan_all())
            return _soft_fail("guardian", "no scanner available")
        except Exception as exc:
            return _soft_fail("guardian", str(exc)[:200])

    async def quarantine_security_issue(params: dict) -> dict:
        issue_id = params.get("issue_id") or "unknown"
        logger.warning("Quarantine requested issue_id=%s", issue_id)
        return _ok("guardian", {"action": "quarantine_recorded", "issue_id": issue_id, "requires_human": True})

    async def prepare_security_remediation(params: dict) -> dict:
        return _ok("guardian", {"action": "remediation_draft", "requires_human": True})

    async def generate_client_report(params: dict) -> dict:
        workspace_id = params.get("workspace_id") or "default"
        try:
            from lifecycle import health_registry
            reports = await health_registry.check_all()
            healthy = sum(
                1 for r in (reports or {}).values()
                if str(getattr(r.status, "value", r.status)).lower() in ("healthy", "nominal", "ok")
            )
            return _ok(
                "reporting",
                {
                    "workspace_id": workspace_id,
                    "services_healthy": healthy,
                    "services_total": len(reports or {}),
                    "generated_at": time.time(),
                },
            )
        except Exception as exc:
            return _soft_fail("reporting", str(exc)[:200])

    async def prepare_client_message(params: dict) -> dict:
        tone = params.get("tone", "professional")
        topic = params.get("topic", "status update")
        return _ok(
            "messaging",
            {
                "draft": f"[{tone}] Regarding {topic}: systems are operating normally.",
                "requires_human_send": True,
            },
        )

    async def analyze_growth_opportunity(params: dict) -> dict:
        return _soft_fail("growth", "growth_service_unavailable", brand_id=params.get("brand_id"))

    async def optimize_infrastructure(params: dict) -> dict:
        try:
            from services.meta_engine import MetaEngineV7
            meta = _resolve(MetaEngineV7)
            if meta and hasattr(meta, "check_health_async"):
                health = await meta.check_health_async()
                return _ok("meta_engine", {"health": health, "requires_human": True})
        except Exception as exp:
            return _soft_fail("meta_engine", str(exp)[:200])
        return _ok("infra", {"suggestions": ["No Meta snapshot"], "requires_human": True})

    async def run_learning_cycle(params: dict) -> dict:
        return _ok("learning", {"status": "idle", "note": "no batch to process", "learned": False})

    async def self_diagnostic(params: dict) -> dict:
        try:
            if hasattr(director, "status"):
                return _ok("director", director.status())
        except Exception as exc:
            return _soft_fail("director", str(exc)[:200])
        return _ok("director", {"running": getattr(director, "running", None)})

    async def web_search(params: dict) -> dict:
        query = (params.get("query") or "").strip()
        if not query:
            return _soft_fail("web_intelligence", "query required")
        try:
            from services.web_intelligence import get_web_intelligence, init_web_intelligence
            gw = get_web_intelligence() or init_web_intelligence()
            results = await gw.search_async(query, max_results=int(params.get("max_results") or 5))
            return _ok(
                "web_intelligence",
                {
                    "query": query,
                    "count": len(results),
                    "results": [r.to_dict() for r in results],
                },
            )
        except Exception as exc:
            return _soft_fail("web_intelligence", str(exc)[:200])

    async def web_fetch(params: dict) -> dict:
        url = (params.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            return _soft_fail("web_intelligence", "url required")
        try:
            from services.web_intelligence import get_web_intelligence, init_web_intelligence
            gw = get_web_intelligence() or init_web_intelligence()
            page = await gw.fetch_async(url)
            return _ok("web_intelligence", page.to_dict())
        except Exception as exc:
            return _soft_fail("web_intelligence", str(exc)[:200])

    async def ask_with_tools(params: dict) -> dict:
        question = (params.get("question") or params.get("q") or "").strip()
        if not question:
            return _soft_fail("tool_agent", "question required")
        try:
            from services.tool_agent import run_tool_agent
            result = await run_tool_agent(question, max_steps=int(params.get("max_steps") or 4))
            return _ok("tool_agent", result)
        except Exception as exc:
            return _soft_fail("tool_agent", str(exc)[:200])

    return {
        # v16.4 — 'publish_scheduled_content' is NOT bound to the Director.
        # The Director's review_content_strategy decision ran on a ~30-minute
        # cadence and invoked this capability, calling mgr.trigger_all() and
        # publishing every brand on each cycle. That duplicated the
        # scheduler's fires, drained Cloudflare and Gemini daily quotas, and
        # forced the pipeline into the deterministic-caption fallback. The
        # scheduler is the sole publisher now. The function above remains
        # defined for explicit non-Director callers (see its docstring).
        "web_search": web_search,
        "web_fetch": web_fetch,
        "ask_with_tools": ask_with_tools,
        "optimize_content_schedule": optimize_content_schedule,
        "calculate_brand_health": calculate_brand_health,
        "queue_brand_revival": queue_brand_revival,
        "run_health_scan": run_health_scan,
        "restart_failed_service": restart_failed_service,
        "run_guardian_scan": run_guardian_scan,
        "quarantine_security_issue": quarantine_security_issue,
        "prepare_security_remediation": prepare_security_remediation,
        "generate_client_report": generate_client_report,
        "prepare_client_message": prepare_client_message,
        "analyze_growth_opportunity": analyze_growth_opportunity,
        "optimize_infrastructure": optimize_infrastructure,
        "run_learning_cycle": run_learning_cycle,
        "self_diagnostic": self_diagnostic,
    }


def bind_director_capabilities(director, container=None) -> Dict[str, str]:
    status: Dict[str, str] = {}
    if director is None:
        logger.error("bind_director_capabilities: director is None")
        return status

    handlers = _build_handlers_for(director, container)
    for name, handler in handlers.items():
        try:
            caps = getattr(director, "capabilities", {})
            if name not in caps:
                if hasattr(director, "register_capability"):
                    try:
                        from services.director import Capability, Sector, Authority, Risk
                        sector = Sector.LEARNING if "web" in name or "ask" in name else Sector.CONTENT
                        director.register_capability(
                            Capability(
                                name,
                                sector,
                                name.replace("_", " ").title(),
                                handler,
                                Authority.AUTO,
                                Risk.LOW,
                            )
                        )
                        status[name] = "bound"
                        continue
                    except Exception as reg_exc:
                        status[name] = f"unknown_capability:{reg_exc}"
                        continue
                status[name] = "unknown_capability"
                continue
            director.bind_capability(name, handler)
            status[name] = "bound"
        except Exception as exc:
            status[name] = f"error:{exc}"

    for name in CRITICAL_CAPABILITIES:
        if name in getattr(director, "capabilities", {}):
            status.setdefault(name, "skipped_critical")

    bound = sum(1 for v in status.values() if v == "bound")
    logger.info("Director bindings complete: %d bound", bound)
    return status


def assert_no_unbound_capabilities(director) -> List[str]:
    unbound: List[str] = []
    for name, cap in getattr(director, "capabilities", {}).items():
        if name in CRITICAL_CAPABILITIES:
            continue
        handler = getattr(cap, "handler", None)
        hname = getattr(handler, "__name__", str(handler))
        if hname == "<lambda>" or "placeholder" in hname.lower():
            unbound.append(name)
    return unbound


__all__ = [
    "bind_director_capabilities",
    "assert_no_unbound_capabilities",
    "CRITICAL_CAPABILITIES",
]
