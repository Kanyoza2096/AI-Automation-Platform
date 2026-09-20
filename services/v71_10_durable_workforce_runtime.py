"""V71.10 — Durable Autonomous Workforce Runtime.

This layer makes autonomous *run state* durable and worker-safe without creating
another governance or execution plane. It owns durable lifecycle/lease state;
actual business side effects continue through the existing governed workflow
runtime, Action Kernel, durable outbox, connector and reconciliation layers.

Important failure rule: an expired autonomous-run lease is NOT permission to
retry an external action. It is converted to reconciliation-required/paused
state so the authoritative execution ledger/outbox can determine whether the
previous worker reached the side-effect boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import asyncio
import os
import socket
import uuid
from typing import Any, Mapping, Optional

from services.v71_9_bounded_autonomous_operations import (
    AutonomousRun, AutonomousRunStatus, AutonomousRunDisposition,
    BoundedAutonomousOperationsRegistry,
)


class DurableRuntimeBlock(str, Enum):
    TENANT_MISMATCH = "tenant_mismatch"
    RUN_NOT_FOUND = "run_not_found"
    DUPLICATE_RUN = "duplicate_run"
    LEASE_NOT_OWNED = "lease_not_owned"
    LEASE_EXPIRED = "lease_expired"
    RUN_NOT_CLAIMABLE = "run_not_claimable"
    STALE_RUN_REQUIRES_RECONCILIATION = "stale_run_requires_reconciliation"
    DURABLE_STORE_REQUIRED = "durable_store_required"
    DURABLE_STORE_UNAVAILABLE = "durable_store_unavailable"
    TERMINAL = "autonomous_run_terminal"
    INVALID_TRANSITION = "invalid_runtime_transition"


@dataclass(frozen=True)
class RuntimeLease:
    run_id: str
    workspace_id: str
    worker_id: str
    lease_token: str
    lease_until: str
    attempt: int


@dataclass(frozen=True)
class DurableRunSnapshot:
    run: AutonomousRun
    lease: Optional[RuntimeLease] = None
    reconciliation_required: bool = False
    authoritative: bool = True


class DurableAutonomousWorkforceRuntime:
    """Durable run coordinator; never invokes providers directly."""

    VERSION = "v71.10"

    def __init__(self, workspace_id: str, operations: BoundedAutonomousOperationsRegistry,
                 db=None, *, allow_in_memory: bool = False, worker_id: Optional[str] = None):
        ws = str(workspace_id or "").strip()
        if not ws or operations.workspace_id != ws:
            raise ValueError(DurableRuntimeBlock.TENANT_MISMATCH.value)
        self.workspace_id = ws
        self.operations = operations
        self.db = db
        self.allow_in_memory = bool(allow_in_memory)
        self.worker_id = worker_id or f"workforce:{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:10]}"
        self._leases: dict[str, RuntimeLease] = {}

    def _db(self):
        if self.db is not None:
            return self.db
        try:
            from services.supabase import supabase_service
            self.db = supabase_service.service_client or supabase_service.client
        except Exception:
            self.db = None
        return self.db

    def _require_store(self):
        db = self._db()
        if db is None and not self.allow_in_memory:
            raise RuntimeError(DurableRuntimeBlock.DURABLE_STORE_REQUIRED.value)
        return db

    @staticmethod
    def _row(data: Any) -> Mapping[str, Any]:
        if isinstance(data, list):
            return data[0] if data else {}
        return data or {}

    @staticmethod
    def _parse_run(row: Mapping[str, Any]) -> AutonomousRun:
        import json
        event = row.get("event_json") or {}
        summary = row.get("result_summary_json") or {}
        if isinstance(event, str):
            event = json.loads(event or "{}")
        if isinstance(summary, str):
            summary = json.loads(summary or "{}")
        return AutonomousRun(
            run_id=str(row["run_id"]), workspace_id=str(row["workspace_id"]),
            agent_id=str(row["agent_id"]), plan_id=str(row["plan_id"]),
            objective=str(row["objective"]), workflow_id=str(row["workflow_id"]),
            trigger=str(row["trigger"]), event=event,
            budget_id=row.get("budget_id"), coordination_id=row.get("coordination_id"),
            takeover_case_id=row.get("takeover_case_id"),
            status=AutonomousRunStatus(str(row["status"])),
            disposition=AutonomousRunDisposition(str(row["disposition"])),
            version=str(row.get("version") or "1"), result_summary=summary,
            run_fingerprint=str(row["run_fingerprint"]),
        )

    async def persist(self, run: AutonomousRun) -> DurableRunSnapshot:
        """Persist a newly-created V71.9 run with an idempotent unique identity."""
        if run.workspace_id != self.workspace_id:
            raise ValueError(DurableRuntimeBlock.TENANT_MISMATCH.value)
        db = self._require_store()
        if db is None:
            # Explicit test/dev mode only.
            self.operations._runs[run.run_id] = run
            return DurableRunSnapshot(run, authoritative=False)
        row = {
            "run_id": run.run_id, "workspace_id": run.workspace_id, "agent_id": run.agent_id,
            "plan_id": run.plan_id, "objective": run.objective, "workflow_id": run.workflow_id,
            "trigger": run.trigger, "event_json": dict(run.event), "budget_id": run.budget_id,
            "coordination_id": run.coordination_id, "takeover_case_id": run.takeover_case_id,
            "status": run.status.value, "disposition": run.disposition.value, "version": run.version,
            "result_summary_json": dict(run.result_summary), "run_fingerprint": run.run_fingerprint,
        }
        try:
            result = await asyncio.to_thread(lambda: db.table("agent_autonomous_runs").insert(row).select("*").execute())
            data = self._row(getattr(result, "data", None))
            if not data:
                raise RuntimeError(DurableRuntimeBlock.DURABLE_STORE_UNAVAILABLE.value)
            authoritative = self._parse_run(data)
            return DurableRunSnapshot(authoritative, authoritative=True)
        except Exception as exc:
            # A unique conflict means the durable identity already exists; return it
            # only after an explicit read. Never create a second process-local run.
            try:
                result = await asyncio.to_thread(lambda: db.table("agent_autonomous_runs").select("*").eq("workspace_id", self.workspace_id).eq("run_id", run.run_id).limit(1).execute())
                data = self._row(getattr(result, "data", None))
                if data:
                    existing = self._parse_run(data)
                    if existing.run_fingerprint != run.run_fingerprint:
                        raise ValueError(DurableRuntimeBlock.DUPLICATE_RUN.value)
                    return DurableRunSnapshot(existing, authoritative=True)
            except ValueError:
                raise
            except Exception:
                pass
            raise RuntimeError(f"{DurableRuntimeBlock.DURABLE_STORE_UNAVAILABLE.value}: {exc}") from exc

    async def get(self, run_id: str) -> DurableRunSnapshot:
        db = self._require_store()
        if db is None:
            run = self.operations.get(run_id)
            if run is None:
                raise ValueError(DurableRuntimeBlock.RUN_NOT_FOUND.value)
            return DurableRunSnapshot(run, self._leases.get(run_id), authoritative=False)
        result = await asyncio.to_thread(lambda: db.table("agent_autonomous_runs").select("*").eq("workspace_id", self.workspace_id).eq("run_id", str(run_id)).limit(1).execute())
        data = self._row(getattr(result, "data", None))
        if not data:
            raise ValueError(DurableRuntimeBlock.RUN_NOT_FOUND.value)
        return DurableRunSnapshot(self._parse_run(data), authoritative=True)

    async def claim(self, run_id: str, *, lease_seconds: int = 300) -> DurableRunSnapshot:
        if lease_seconds < 30 or lease_seconds > 3600:
            raise ValueError("lease_seconds must be between 30 and 3600")
        db = self._require_store()
        if db is None:
            run = self.operations.get(run_id)
            if run is None:
                raise ValueError(DurableRuntimeBlock.RUN_NOT_FOUND.value)
            if run.status != AutonomousRunStatus.READY:
                raise ValueError(DurableRuntimeBlock.RUN_NOT_CLAIMABLE.value)
            lease = RuntimeLease(str(run_id), self.workspace_id, self.worker_id,
                                uuid.uuid4().hex, (datetime.now(timezone.utc).timestamp() + lease_seconds).__str__(), 1)
            self._leases[str(run_id)] = lease
            run = self.operations._replace(run, status=AutonomousRunStatus.RUNNING)
            return DurableRunSnapshot(run, lease, authoritative=False)
        try:
            result = await asyncio.to_thread(lambda: db.rpc("claim_agent_autonomous_run", {
                "p_workspace_id": self.workspace_id, "p_run_id": str(run_id),
                "p_worker_id": self.worker_id, "p_lease_seconds": int(lease_seconds),
            }).execute())
            data = self._row(getattr(result, "data", None))
            if not data:
                raise ValueError(DurableRuntimeBlock.RUN_NOT_CLAIMABLE.value)
            run = self._parse_run(data)
            lease = RuntimeLease(run.run_id, self.workspace_id, self.worker_id,
                                 str(data["lease_token"]), str(data["lease_until"]), int(data.get("attempt") or 1))
            return DurableRunSnapshot(run, lease, authoritative=True)
        except ValueError:
            raise
        except Exception as exc:
            raise RuntimeError(f"{DurableRuntimeBlock.DURABLE_STORE_UNAVAILABLE.value}: {exc}") from exc

    async def renew(self, lease: RuntimeLease, *, lease_seconds: int = 300) -> RuntimeLease:
        if lease.workspace_id != self.workspace_id or lease.worker_id != self.worker_id:
            raise ValueError(DurableRuntimeBlock.LEASE_NOT_OWNED.value)
        db = self._require_store()
        if db is None:
            current = self._leases.get(lease.run_id)
            if not current or current.lease_token != lease.lease_token:
                raise ValueError(DurableRuntimeBlock.LEASE_NOT_OWNED.value)
            updated = RuntimeLease(lease.run_id, lease.workspace_id, lease.worker_id,
                                   lease.lease_token, str(datetime.now(timezone.utc).timestamp() + lease_seconds), lease.attempt)
            self._leases[lease.run_id] = updated
            return updated
        result = await asyncio.to_thread(lambda: db.rpc("renew_agent_autonomous_run_lease", {
            "p_workspace_id": self.workspace_id, "p_run_id": lease.run_id,
            "p_worker_id": self.worker_id, "p_lease_token": lease.lease_token,
            "p_lease_seconds": int(lease_seconds),
        }).execute())
        data = self._row(getattr(result, "data", None))
        if not data:
            raise ValueError(DurableRuntimeBlock.LEASE_NOT_OWNED.value)
        return RuntimeLease(lease.run_id, self.workspace_id, self.worker_id,
                            lease.lease_token, str(data["lease_until"]), int(data.get("attempt") or lease.attempt))

    async def finalize(self, lease: RuntimeLease, *, status: AutonomousRunStatus,
                       disposition: Optional[AutonomousRunDisposition] = None,
                       result_summary: Optional[Mapping[str, Any]] = None) -> DurableRunSnapshot:
        if status not in {AutonomousRunStatus.COMPLETED, AutonomousRunStatus.FAILED,
                          AutonomousRunStatus.BLOCKED, AutonomousRunStatus.PAUSED}:
            raise ValueError(DurableRuntimeBlock.INVALID_TRANSITION.value)
        db = self._require_store()
        if db is None:
            current_lease = self._leases.get(lease.run_id)
            if current_lease is None or current_lease.lease_token != lease.lease_token or current_lease.worker_id != self.worker_id:
                raise ValueError(DurableRuntimeBlock.LEASE_NOT_OWNED.value)
            run = self.operations.get(lease.run_id)
            if run is None:
                raise ValueError(DurableRuntimeBlock.RUN_NOT_FOUND.value)
            if run.status in {AutonomousRunStatus.COMPLETED, AutonomousRunStatus.FAILED, AutonomousRunStatus.CANCELLED}:
                raise ValueError(DurableRuntimeBlock.TERMINAL.value)
            updated = self.operations._replace(run, status=status,
                disposition=disposition or run.disposition,
                result_summary=dict(result_summary or run.result_summary))
            self._leases.pop(lease.run_id, None)
            return DurableRunSnapshot(updated, authoritative=False,
                                      reconciliation_required=status == AutonomousRunStatus.PAUSED)
        import json
        result = await asyncio.to_thread(lambda: db.rpc("finalize_agent_autonomous_run", {
            "p_workspace_id": self.workspace_id, "p_run_id": lease.run_id,
            "p_worker_id": self.worker_id, "p_lease_token": lease.lease_token,
            "p_status": status.value, "p_disposition": (disposition.value if disposition else None),
            "p_result_summary": dict(result_summary or {}),
        }).execute())
        data = self._row(getattr(result, "data", None))
        if not data:
            raise ValueError(DurableRuntimeBlock.LEASE_NOT_OWNED.value)
        return DurableRunSnapshot(self._parse_run(data), authoritative=True,
                                  reconciliation_required=status == AutonomousRunStatus.PAUSED)

    async def mark_stale_for_reconciliation(self, run_id: str) -> DurableRunSnapshot:
        """Fence an expired worker without retrying its business side effect."""
        db = self._require_store()
        if db is None:
            run = self.operations.get(run_id)
            if run is None:
                raise ValueError(DurableRuntimeBlock.RUN_NOT_FOUND.value)
            if run.status != AutonomousRunStatus.RUNNING:
                raise ValueError(DurableRuntimeBlock.STALE_RUN_REQUIRES_RECONCILIATION.value)
            updated = self.operations._replace(run, status=AutonomousRunStatus.PAUSED,
                result_summary={**run.result_summary, "reconciliation_required": True,
                                "reason": DurableRuntimeBlock.LEASE_EXPIRED.value})
            return DurableRunSnapshot(updated, authoritative=False, reconciliation_required=True)
        result = await asyncio.to_thread(lambda: db.rpc("mark_agent_autonomous_run_uncertain", {
            "p_workspace_id": self.workspace_id, "p_run_id": str(run_id),
        }).execute())
        data = self._row(getattr(result, "data", None))
        if not data:
            raise ValueError(DurableRuntimeBlock.STALE_RUN_REQUIRES_RECONCILIATION.value)
        return DurableRunSnapshot(self._parse_run(data), authoritative=True, reconciliation_required=True)

    async def execute_claimed(self, lease: RuntimeLease, *, envelope: Any,
                              workflow: Mapping[str, Any], engine: Any,
                              approved: bool = False, budget_actions: int = 1,
                              budget_cost: float = 0.0, budget_risk_units: float = 0.0) -> Any:
        """Execute exactly once from a durable claim; providers remain downstream."""
        snap = await self.get(lease.run_id)
        # Hydrate the V71.9 facade from the authoritative durable row. The facade
        # remains the only path into business governance/execution; this is not a
        # second copy of run authority.
        self.operations._runs[lease.run_id] = snap.run
        # Reuse V71.9's governed admission/execution path. This method deliberately
        # does not call any connector/provider itself.
        try:
            result = await self.operations.execute(
                lease.run_id, envelope=envelope, workflow=workflow, engine=engine,
                approved=approved, budget_actions=budget_actions,
                budget_cost=budget_cost, budget_risk_units=budget_risk_units,
            )
            await self.finalize(lease, status=AutonomousRunStatus.COMPLETED,
                                disposition=AutonomousRunDisposition.EXECUTION_CANDIDATE,
                                result_summary={"execution_result": _safe_summary(result)})
            return result
        except Exception as exc:
            # If the process is still alive and the exact lease is owned, this is a
            # known application failure. If lease ownership is lost, do NOT overwrite
            # another worker's state; leave reconciliation to the durable control plane.
            try:
                await self.finalize(lease, status=AutonomousRunStatus.FAILED,
                                    result_summary={"error_type": type(exc).__name__, "error": str(exc)[:1000]})
            except Exception:
                pass
            raise


def _safe_summary(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(k): _safe_summary(v) for k, v in list(value.items())[:50]}
    if isinstance(value, (list, tuple)):
        return [_safe_summary(v) for v in value[:50]]
    return str(value)


__all__ = ["DurableRuntimeBlock", "RuntimeLease", "DurableRunSnapshot", "DurableAutonomousWorkforceRuntime"]
