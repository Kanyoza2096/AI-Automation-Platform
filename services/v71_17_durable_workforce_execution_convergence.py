"""V71.17 — Durable Workforce Execution Convergence.

Binds a durable V71 workforce run to the existing governed execution identity.
This module is an execution handoff/evidence adapter, not a second executor.
The actual side effect remains BusinessAutomation -> Action Kernel -> durable
outbox/execution ledger -> connector/provider.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional
import asyncio
import hashlib
import json
import logging
import uuid

from services.v71_9_bounded_autonomous_operations import (
    AutonomousRunStatus,
    AutonomousRunDisposition,
)

logger = logging.getLogger("kanyoza.v71.17")


@dataclass(frozen=True)
class ExecutionHandoff:
    handoff_id: str
    run_id: str
    workspace_id: str
    correlation_id: str
    status: str
    execution_ids: tuple[str, ...] = ()
    intent_ids: tuple[str, ...] = ()
    result_summary: Mapping[str, Any] = None
    fingerprint: str = ""

    def __post_init__(self):
        if self.result_summary is None:
            object.__setattr__(self, "result_summary", {})

    def canonical(self) -> dict[str, Any]:
        return {
            "handoff_id": self.handoff_id,
            "run_id": self.run_id,
            "workspace_id": self.workspace_id,
            "correlation_id": self.correlation_id,
            "status": self.status,
            "execution_ids": list(self.execution_ids),
            "intent_ids": list(self.intent_ids),
            "result_summary": dict(self.result_summary),
        }

    def with_fingerprint(self) -> "ExecutionHandoff":
        raw = json.dumps(self.canonical(), sort_keys=True, default=str, separators=(",", ":"))
        return ExecutionHandoff(**{**self.__dict__, "fingerprint": hashlib.sha256(raw.encode()).hexdigest()})


class DurableWorkforceExecutionConvergence:
    VERSION = "v71.17"

    def __init__(self, *, production_runtime: Any, db: Any = None):
        if production_runtime is None:
            raise ValueError("production runtime is required")
        self.production_runtime = production_runtime
        self.db = db
        self._memory: dict[str, ExecutionHandoff] = {}

    def _db(self):
        if self.db is not None:
            return self.db
        try:
            from services.supabase import supabase_service
            self.db = supabase_service.service_client or supabase_service.client
        except Exception:
            self.db = None
        return self.db

    @staticmethod
    def _row(data: Any) -> Mapping[str, Any]:
        if isinstance(data, list):
            return data[0] if data else {}
        return data or {}

    @staticmethod
    def _parse(row: Mapping[str, Any]) -> ExecutionHandoff:
        def seq(value):
            if value is None: return ()
            if isinstance(value, str):
                try: value = json.loads(value)
                except Exception: value = [value]
            return tuple(str(x) for x in (value or ()))
        summary = row.get("result_summary_json") or {}
        if isinstance(summary, str): summary = json.loads(summary or "{}")
        return ExecutionHandoff(
            handoff_id=str(row["handoff_id"]), run_id=str(row["run_id"]),
            workspace_id=str(row["workspace_id"]), correlation_id=str(row["correlation_id"]),
            status=str(row["status"]), execution_ids=seq(row.get("execution_ids_json")),
            intent_ids=seq(row.get("intent_ids_json")), result_summary=summary,
            fingerprint=str(row.get("handoff_fingerprint") or ""),
        )

    async def begin(self, *, run_id: str, workspace_id: str) -> ExecutionHandoff:
        run_id, workspace_id = str(run_id).strip(), str(workspace_id).strip()
        if not run_id or not workspace_id:
            raise ValueError("run_id and workspace_id are required")
        handoff_id = "hnd_" + hashlib.sha256(f"{workspace_id}|{run_id}".encode()).hexdigest()[:24]
        correlation_id = f"workforce:{workspace_id}:{run_id}"
        draft = ExecutionHandoff(handoff_id, run_id, workspace_id, correlation_id, "pending").with_fingerprint()
        db = self._db()
        if db is None:
            existing = self._memory.get(handoff_id)
            if existing: return existing
            self._memory[handoff_id] = draft
            return draft
        row = {"handoff_id": handoff_id, "run_id": run_id, "workspace_id": workspace_id,
               "correlation_id": correlation_id, "status": "pending", "execution_ids_json": [],
               "intent_ids_json": [], "result_summary_json": {}, "handoff_fingerprint": draft.fingerprint,
               "created_at": datetime.now(timezone.utc).isoformat(), "updated_at": datetime.now(timezone.utc).isoformat()}
        try:
            result = await asyncio.to_thread(lambda: db.table("agent_execution_handoffs").insert(row).select("*").execute())
            data = self._row(getattr(result, "data", None))
            if data: return self._parse(data)
        except Exception:
            result = await asyncio.to_thread(lambda: db.table("agent_execution_handoffs").select("*").eq("workspace_id", workspace_id).eq("run_id", run_id).limit(1).execute())
            data = self._row(getattr(result, "data", None))
            if data: return self._parse(data)
            raise
        raise RuntimeError("durable workforce execution handoff was not persisted")

    async def claim(self, handoff: ExecutionHandoff) -> ExecutionHandoff:
        db = self._db()
        if db is None:
            current = self._memory.get(handoff.handoff_id, handoff)
            if current.status != "pending": return current
            updated = ExecutionHandoff(**{**current.__dict__, "status": "dispatching"}).with_fingerprint()
            self._memory[handoff.handoff_id] = updated
            return updated
        result = await asyncio.to_thread(lambda: db.rpc("claim_agent_execution_handoff", {
            "p_workspace_id": handoff.workspace_id, "p_handoff_id": handoff.handoff_id,
        }).execute())
        data = self._row(getattr(result, "data", None))
        if not data:
            existing = await asyncio.to_thread(lambda: db.table("agent_execution_handoffs").select("*").eq("workspace_id", handoff.workspace_id).eq("handoff_id", handoff.handoff_id).limit(1).execute())
            data = self._row(getattr(existing, "data", None))
        if not data: raise RuntimeError("execution handoff claim unavailable")
        return self._parse(data)

    async def _capture_action_evidence(self, workspace_id: str, correlation_id: str) -> tuple[tuple[str, ...], tuple[str, ...], dict[str, Any]]:
        db = self._db()
        if db is None:
            return (), (), {"durable_action_evidence": "not_configured"}
        try:
            out = await asyncio.to_thread(lambda: db.table("action_outbox").select("intent_id,execution_id,status").eq("workspace_id", workspace_id).eq("correlation_id", correlation_id).limit(100).execute())
            rows = getattr(out, "data", None) or []
            intents = tuple(sorted({str(r.get("intent_id")) for r in rows if r.get("intent_id")}))
            executions = tuple(sorted({str(r.get("execution_id")) for r in rows if r.get("execution_id")}))
            statuses = tuple(sorted({str(r.get("status") or "") for r in rows}))
            return executions, intents, {"action_outbox_rows": len(rows), "action_outbox_statuses": statuses}
        except Exception as exc:
            return (), (), {"durable_action_evidence": "unavailable", "error": type(exc).__name__}

    async def finalize(self, handoff: ExecutionHandoff, *, status: str, result_summary: Mapping[str, Any] = None) -> ExecutionHandoff:
        allowed = {"accepted", "awaiting_execution", "completed", "failed", "uncertain", "blocked"}
        if status not in allowed: raise ValueError("invalid execution handoff status")
        summary = dict(result_summary or {})
        executions, intents, evidence = await self._capture_action_evidence(handoff.workspace_id, handoff.correlation_id)
        summary.update(evidence)
        updated = ExecutionHandoff(**{**handoff.__dict__, "status": status,
            "execution_ids": tuple(sorted(set(handoff.execution_ids) | set(executions))),
            "intent_ids": tuple(sorted(set(handoff.intent_ids) | set(intents))),
            "result_summary": summary}).with_fingerprint()
        db = self._db()
        if db is None:
            self._memory[handoff.handoff_id] = updated
            return updated
        result = await asyncio.to_thread(lambda: db.table("agent_execution_handoffs").update({
            "status": status, "execution_ids_json": list(updated.execution_ids),
            "intent_ids_json": list(updated.intent_ids), "result_summary_json": summary,
            "handoff_fingerprint": updated.fingerprint, "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("workspace_id", handoff.workspace_id).eq("handoff_id", handoff.handoff_id).eq("status", handoff.status).execute())
        if not getattr(result, "data", None):
            existing = await asyncio.to_thread(lambda: db.table("agent_execution_handoffs").select("*").eq("workspace_id", handoff.workspace_id).eq("handoff_id", handoff.handoff_id).limit(1).execute())
            data = self._row(getattr(existing, "data", None))
            if data: return self._parse(data)
            raise RuntimeError("execution handoff finalization lost ownership")
        return updated

    async def dispatch_claimed(self, handoff: ExecutionHandoff, *, durable_runtime: Any,
                               envelope: Any, workflow: Mapping[str, Any], engine: Any,
                               approved: bool = False, budget_actions: int = 1,
                               budget_cost: float = 0.0, budget_risk_units: float = 0.0) -> Any:
        """Dispatch one claimed workforce run through the pre-existing V71.10 -> V71.9 -> V70 path."""
        if handoff.status != "dispatching":
            raise ValueError("handoff is not owned for dispatch")
        snap = await durable_runtime.get(handoff.run_id)
        if snap.run.workspace_id != handoff.workspace_id:
            raise ValueError("tenant mismatch")
        event = dict(snap.run.event)
        event["run_id"] = snap.run.run_id
        event["workforce_run_id"] = snap.run.run_id
        event["correlation_id"] = handoff.correlation_id
        # V71.10 remains the sole run lease/finalization authority. We deliberately
        # reuse it instead of introducing another execution worker or provider call.
        lease = snap.lease
        if lease is None:
            raise ValueError("durable workforce lease required")
        try:
            result = await durable_runtime.execute_claimed(
                lease, envelope=envelope,
                workflow={**dict(workflow), "event": event},
                engine=engine, approved=approved,
                budget_actions=budget_actions, budget_cost=budget_cost,
                budget_risk_units=budget_risk_units,
            )
            result_status = str((result or {}).get("status") if isinstance(result, Mapping) else "completed").lower()
            if result_status in {"pending", "in_progress", "approval_required"}:
                return await self.finalize(handoff, status="awaiting_execution", result_summary={"result": result})
            if result_status in {"uncertain", "reconciliation_required"}:
                return await self.finalize(handoff, status="uncertain", result_summary={"result": result})
            return await self.finalize(handoff, status="completed", result_summary={"result": result})
        except Exception as exc:
            await self.finalize(handoff, status="failed", result_summary={"error_type": type(exc).__name__, "error": str(exc)[:500]})
            raise

    def status(self) -> dict[str, Any]:
        return {
            "version": self.VERSION,
            "canonical": True,
            "role": ["execution_handoff", "evidence_linkage", "reconciliation_signal"],
            "parallel_executor": False,
            "provider_calls": False,
            "action_kernel_reused": True,
            "durable_outbox_reused": True,
            "canonical_path": "V71 workforce run -> durable handoff -> V71.10 lease -> V71.9 governance -> V70 BusinessAutomation -> Action Kernel -> durable outbox/execution ledger -> connector/provider -> reconciliation",
            "certification": "NOT_PROVEN",
        }


def install_durable_workforce_execution_convergence(app: Any, container: Any) -> DurableWorkforceExecutionConvergence:
    production_runtime = getattr(app.state, "production_runtime", None)
    if production_runtime is None:
        raise RuntimeError("V71.13 production runtime must be installed first")
    adapter = DurableWorkforceExecutionConvergence(production_runtime=production_runtime)
    app.state.workforce_execution_convergence = adapter
    logger.info("V71.17 durable workforce execution convergence installed")
    return adapter


__all__ = ["ExecutionHandoff", "DurableWorkforceExecutionConvergence", "install_durable_workforce_execution_convergence"]
