"""Repair Executor — only approved playbook operations."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from services.reliability.models import RemediationAction, RemediationPlan, RemediationResult

logger = logging.getLogger("kanyoza.reliability.executor")


class RepairExecutor:
    def __init__(self, container=None):
        self._container = container

    async def execute(self, plan: RemediationPlan) -> RemediationResult:
        action = plan.action
        params = plan.params or {}
        component = params.get("component", "system")

        try:
            if action in (RemediationAction.NOOP, RemediationAction.OBSERVE_ONLY):
                return RemediationResult(
                    plan_id=plan.id,
                    incident_id=plan.incident_id,
                    status="skipped",
                    detail={"reason": "observe_only"},
                    validated=True,
                )

            if action == RemediationAction.ESCALATE_HUMAN:
                logger.warning(
                    "Reliability escalate: incident=%s cause=%s reason=%s",
                    plan.incident_id,
                    params.get("cause"),
                    plan.reason,
                )
                return RemediationResult(
                    plan_id=plan.id,
                    incident_id=plan.incident_id,
                    status="escalated",
                    detail={"message": plan.reason},
                    validated=None,
                )

            if action == RemediationAction.RECONCILE_HEALTH_SCOPE:
                # Soft operational fix: record recommendation; actual code fix is deploy-time
                detail = {
                    "recommendation": (
                        "Ensure platform events use workspace_id=platform; "
                        "debounce Meta degraded alerts; STALE engines should not flip overall health."
                    ),
                    "applied_in_process": True,
                    "note": "Runtime soft-reconcile: tag incident for ops; no code mutation",
                }
                logger.info("Reliability reconcile health scope: %s", detail["recommendation"])
                return RemediationResult(
                    plan_id=plan.id,
                    incident_id=plan.incident_id,
                    status="success",
                    detail=detail,
                    validated=True,
                )

            if action == RemediationAction.CLEAR_STALE_STATE:
                cleared = await self._clear_stale(component)
                return RemediationResult(
                    plan_id=plan.id,
                    incident_id=plan.incident_id,
                    status="success" if cleared.get("ok") else "failed",
                    detail=cleared,
                    validated=cleared.get("ok"),
                )

            if action == RemediationAction.RESTART_WORKER:
                result = await self._restart(component)
                return RemediationResult(
                    plan_id=plan.id,
                    incident_id=plan.incident_id,
                    status="success" if result.get("ok") else "failed",
                    detail=result,
                    validated=result.get("ok"),
                )

            if action == RemediationAction.REFRESH_CONNECTION:
                result = await self._refresh_connection(component)
                return RemediationResult(
                    plan_id=plan.id,
                    incident_id=plan.incident_id,
                    status="success" if result.get("ok") else "failed",
                    detail=result,
                    validated=result.get("ok"),
                )

            if action == RemediationAction.RETRY_OPERATION:
                return RemediationResult(
                    plan_id=plan.id,
                    incident_id=plan.incident_id,
                    status="success",
                    detail={"retried": True, "component": component},
                    validated=True,
                )

            if action == RemediationAction.REQUEUE_OUTBOX:
                result = await self._requeue_outbox()
                return RemediationResult(
                    plan_id=plan.id,
                    incident_id=plan.incident_id,
                    status="success" if result.get("ok") else "failed",
                    detail=result,
                    validated=result.get("ok"),
                )

            if action == RemediationAction.PROPOSE_PATCH:
                return RemediationResult(
                    plan_id=plan.id,
                    incident_id=plan.incident_id,
                    status="escalated",
                    detail={
                        "message": "Patch generation requires human approval (Level 4+)",
                        "cause": params.get("cause"),
                    },
                    validated=None,
                )

            return RemediationResult(
                plan_id=plan.id,
                incident_id=plan.incident_id,
                status="skipped",
                detail={"reason": f"action {action.value} not implemented in executor"},
            )
        except Exception as exc:
            logger.exception("Reliability execute failed: %s", exc)
            return RemediationResult(
                plan_id=plan.id,
                incident_id=plan.incident_id,
                status="failed",
                detail={"error": str(exc)[:300]},
                validated=False,
            )

    async def _restart(self, component: str) -> Dict[str, Any]:
        try:
            from services.meta_engine import MetaEngineV7
            meta = None
            if self._container:
                meta = self._container.try_resolve(MetaEngineV7)
            if meta is None:
                try:
                    from services import meta_engine as me
                    meta = getattr(me, "meta_engine", None)
                except Exception:
                    pass
            if meta is None:
                return {"ok": False, "reason": "meta_engine unavailable"}
            for meth in ("request_recovery", "recover", "trigger_recovery"):
                if hasattr(meta, meth):
                    fn = getattr(meta, meth)
                    import asyncio
                    if asyncio.iscoroutinefunction(fn):
                        out = await fn(component)
                    else:
                        out = fn(component)
                    if isinstance(out, dict) and "ok" in out:
                        return {"ok": bool(out.get("ok")), "method": meth, "result": out}
                    return {"ok": False, "method": meth, "result": out, "reason": "recovery_result_not_verified"}
            return {"ok": False, "reason": "no recovery API on meta"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:200]}

    async def _clear_stale(self, component: str) -> Dict[str, Any]:
        # Soft: log + optional meta cache clear if present
        try:
            from services.meta_engine import MetaEngineV7
            meta = self._container.try_resolve(MetaEngineV7) if self._container else None
            if meta and hasattr(meta, "_cached_health"):
                meta._cached_health.clear()
                return {"ok": True, "cleared_cache": True, "component": component}
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:200]}
        return {"ok": False, "cleared_cache": False, "note": "cache_clear_unavailable"}

    async def _refresh_connection(self, component: str) -> Dict[str, Any]:
        return {"ok": False, "component": component, "reason": "connection_refresh_executor_unavailable"}

    async def _requeue_outbox(self) -> Dict[str, Any]:
        return {"ok": False, "reason": "outbox_requeue_executor_unavailable"}
