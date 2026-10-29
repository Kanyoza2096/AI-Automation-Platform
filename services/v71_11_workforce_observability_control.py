"""V71.11 — Autonomous Workforce Observability & Control Plane.

Read/control plane only. It provides operator-visible state, durable control
commands and audit evidence without becoming a second governance or execution
engine. Control commands can pause/cancel/fence a run, but resume always returns
to the existing V71.9 governance admission path.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import asyncio, hashlib, json, os, socket, uuid
from typing import Any, Mapping, Optional

from services.v71_9_bounded_autonomous_operations import (
    AutonomousRun, AutonomousRunStatus, AutonomousRunDisposition,
    BoundedAutonomousOperationsRegistry,
)


class ControlCommand(str, Enum):
    PAUSE = "pause"
    RESUME = "resume"
    CANCEL = "cancel"
    FENCE = "fence"


class ControlBlock(str, Enum):
    TENANT_MISMATCH = "tenant_mismatch"
    RUN_NOT_FOUND = "run_not_found"
    RUN_TAMPERED = "run_tampered"
    TERMINAL = "autonomous_run_terminal"
    INVALID_CONTROL = "invalid_control"
    RESUME_REQUIRES_RECONCILIATION = "resume_requires_reconciliation"
    RESUME_REQUIRES_GOVERNANCE = "resume_requires_governance"
    CONTROL_NOT_APPLIED = "control_not_applied"
    DURABLE_STORE_REQUIRED = "durable_store_required"
    DURABLE_STORE_UNAVAILABLE = "durable_store_unavailable"
    LEASE_NOT_OWNED = "lease_not_owned"


@dataclass(frozen=True)
class WorkforceEvent:
    event_id: str
    workspace_id: str
    run_id: str
    event_type: str
    actor_type: str
    actor_id: str
    payload: Mapping[str, Any]
    occurred_at: str
    fingerprint: str

    def canonical(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id, "workspace_id": self.workspace_id,
            "run_id": self.run_id, "event_type": self.event_type,
            "actor_type": self.actor_type, "actor_id": self.actor_id,
            "payload": dict(self.payload), "occurred_at": self.occurred_at,
        }

    def calculate_fingerprint(self) -> str:
        raw = json.dumps(self.canonical(), sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True)
class WorkforceSnapshot:
    run: AutonomousRun
    health: str
    controls_available: tuple[str, ...]
    reconciliation_required: bool
    lease_owner: Optional[str] = None
    lease_until: Optional[str] = None
    attempt: int = 0
    last_error: Optional[str] = None
    authoritative: bool = True


class WorkforceObservabilityControlPlane:
    VERSION = "v71.11"

    def __init__(self, workspace_id: str, operations: BoundedAutonomousOperationsRegistry,
                 db=None, *, allow_in_memory: bool = False, operator_id: str = "operator"):
        ws = str(workspace_id or "").strip()
        if not ws or operations.workspace_id != ws:
            raise ValueError(ControlBlock.TENANT_MISMATCH.value)
        self.workspace_id = ws
        self.operations = operations
        self.db = db
        self.allow_in_memory = bool(allow_in_memory)
        self.operator_id = str(operator_id or "operator")
        self._events: dict[str, WorkforceEvent] = {}

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
            raise RuntimeError(ControlBlock.DURABLE_STORE_REQUIRED.value)
        return db

    def _assert_run(self, run: AutonomousRun) -> None:
        if run.workspace_id != self.workspace_id:
            raise ValueError(ControlBlock.TENANT_MISMATCH.value)
        self.operations.assert_integrity(run)

    @staticmethod
    def _health(run: AutonomousRun, reconciliation_required: bool = False) -> str:
        if reconciliation_required or run.status == AutonomousRunStatus.PAUSED:
            return "RECONCILIATION_REQUIRED" if reconciliation_required else "PAUSED"
        if run.status == AutonomousRunStatus.RUNNING:
            return "RUNNING"
        if run.status == AutonomousRunStatus.READY:
            return "READY"
        if run.status == AutonomousRunStatus.BLOCKED:
            return "BLOCKED"
        if run.status == AutonomousRunStatus.FAILED:
            return "FAILED"
        if run.status == AutonomousRunStatus.COMPLETED:
            return "COMPLETED"
        if run.status == AutonomousRunStatus.CANCELLED:
            return "CANCELLED"
        return "PROPOSED"

    def _controls(self, run: AutonomousRun, reconciliation_required: bool) -> tuple[str, ...]:
        if run.status == AutonomousRunStatus.RUNNING:
            return (ControlCommand.PAUSE.value, ControlCommand.FENCE.value)
        if run.status == AutonomousRunStatus.READY:
            return (ControlCommand.PAUSE.value, ControlCommand.CANCEL.value)
        if run.status == AutonomousRunStatus.PAUSED:
            if reconciliation_required:
                return (ControlCommand.FENCE.value,)
            return (ControlCommand.RESUME.value, ControlCommand.CANCEL.value)
        if run.status in {AutonomousRunStatus.PROPOSED, AutonomousRunStatus.BLOCKED}:
            return (ControlCommand.CANCEL.value,)
        return ()

    async def snapshot(self, run_id: str) -> WorkforceSnapshot:
        db = self._require_store()
        if db is None:
            run = self.operations.get(run_id)
            if run is None:
                raise ValueError(ControlBlock.RUN_NOT_FOUND.value)
            self._assert_run(run)
            rr = bool(run.result_summary.get("reconciliation_required"))
            lease = None
            try:
                lease = self.operations  # keep local mode intentionally read-only
            except Exception:
                pass
            return WorkforceSnapshot(run, self._health(run, rr), self._controls(run, rr), rr, authoritative=False)
        result = await asyncio.to_thread(lambda: db.table("agent_autonomous_runs").select("*").eq("workspace_id", self.workspace_id).eq("run_id", str(run_id)).limit(1).execute())
        data = result.data[0] if getattr(result, "data", None) else None
        if not data:
            raise ValueError(ControlBlock.RUN_NOT_FOUND.value)
        run = _parse_run(data)
        self._assert_run(run)
        rr = bool(data.get("reconciliation_required"))
        return WorkforceSnapshot(run, self._health(run, rr), self._controls(run, rr), rr,
                                 data.get("lease_owner"), data.get("lease_until"), int(data.get("attempt") or 0),
                                 data.get("last_error"), True)

    async def list_runs(self, *, statuses: Optional[tuple[AutonomousRunStatus, ...]] = None,
                        limit: int = 100) -> tuple[WorkforceSnapshot, ...]:
        if limit < 1 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        db = self._require_store()
        if db is None:
            values = list(self.operations._runs.values())
            if statuses:
                values = [r for r in values if r.status in set(statuses)]
            return tuple(WorkforceSnapshot(r, self._health(r, bool(r.result_summary.get("reconciliation_required"))),
                                           self._controls(r, bool(r.result_summary.get("reconciliation_required"))),
                                           bool(r.result_summary.get("reconciliation_required")), authoritative=False)
                         for r in values[:limit])
        q = db.table("agent_autonomous_runs").select("*").eq("workspace_id", self.workspace_id).limit(limit)
        if statuses:
            q = q.in_("status", [s.value for s in statuses])
        result = await asyncio.to_thread(lambda: q.execute())
        out = []
        for row in (getattr(result, "data", None) or []):
            run = _parse_run(row); self._assert_run(run)
            rr = bool(row.get("reconciliation_required"))
            out.append(WorkforceSnapshot(run, self._health(run, rr), self._controls(run, rr), rr,
                                         row.get("lease_owner"), row.get("lease_until"), int(row.get("attempt") or 0),
                                         row.get("last_error"), True))
        return tuple(out)

    async def control(self, run_id: str, command: ControlCommand, *, reason: str = "") -> WorkforceSnapshot:
        command = ControlCommand(command)
        reason = str(reason or "operator_control").strip()[:1000]
        db = self._require_store()
        if db is None:
            run = self.operations.get(run_id)
            if run is None: raise ValueError(ControlBlock.RUN_NOT_FOUND.value)
            self._assert_run(run)
            if run.status in {AutonomousRunStatus.COMPLETED, AutonomousRunStatus.FAILED, AutonomousRunStatus.CANCELLED}:
                raise ValueError(ControlBlock.TERMINAL.value)
            if command == ControlCommand.RESUME:
                if run.status != AutonomousRunStatus.PAUSED:
                    raise ValueError(ControlBlock.INVALID_CONTROL.value)
                if run.result_summary.get("reconciliation_required"):
                    raise ValueError(ControlBlock.RESUME_REQUIRES_RECONCILIATION.value)
                updated = self.operations._replace(run, status=AutonomousRunStatus.READY,
                    result_summary={**run.result_summary, "resumed_by": self.operator_id})
            elif command in {ControlCommand.PAUSE, ControlCommand.FENCE}:
                updated = self.operations._replace(run, status=AutonomousRunStatus.PAUSED,
                    result_summary={**run.result_summary, "reconciliation_required": run.status == AutonomousRunStatus.RUNNING,
                                    "operator_control": command.value, "reason": reason})
            elif command == ControlCommand.CANCEL:
                updated = self.operations._replace(run, status=AutonomousRunStatus.CANCELLED,
                    result_summary={**run.result_summary, "cancelled_by": self.operator_id, "reason": reason})
            else:
                raise ValueError(ControlBlock.INVALID_CONTROL.value)
            await self._record_event(updated.run_id, f"control.{command.value}", {"reason": reason})
            rr = bool(updated.result_summary.get("reconciliation_required"))
            return WorkforceSnapshot(updated, self._health(updated, rr), self._controls(updated, rr), rr, authoritative=False)

        rpc_name = {
            ControlCommand.PAUSE: "pause_agent_autonomous_run",
            ControlCommand.RESUME: "resume_agent_autonomous_run",
            ControlCommand.CANCEL: "cancel_agent_autonomous_run",
            ControlCommand.FENCE: "fence_agent_autonomous_run",
        }[command]
        result = await asyncio.to_thread(lambda: db.rpc(rpc_name, {
            "p_workspace_id": self.workspace_id, "p_run_id": str(run_id),
            "p_actor_id": self.operator_id, "p_reason": reason,
        }).execute())
        data = (getattr(result, "data", None) or [])
        row = data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else None)
        if not row:
            if command == ControlCommand.RESUME:
                raise ValueError(ControlBlock.RESUME_REQUIRES_RECONCILIATION.value)
            raise ValueError(ControlBlock.CONTROL_NOT_APPLIED.value)
        run = _parse_run(row); self._assert_run(run)
        rr = bool(row.get("reconciliation_required"))
        await self._record_event(run.run_id, f"control.{command.value}", {"reason": reason})
        return WorkforceSnapshot(run, self._health(run, rr), self._controls(run, rr), rr,
                                 row.get("lease_owner"), row.get("lease_until"), int(row.get("attempt") or 0),
                                 row.get("last_error"), True)

    async def _record_event(self, run_id: str, event_type: str, payload: Mapping[str, Any]) -> WorkforceEvent:
        now = datetime.now(timezone.utc).isoformat()
        event = WorkforceEvent(uuid.uuid4().hex, self.workspace_id, str(run_id), event_type,
                               "operator", self.operator_id, dict(payload), now, "")
        event = WorkforceEvent(**{**event.__dict__, "fingerprint": event.calculate_fingerprint()})
        db = self._db()
        if db is None:
            self._events[event.event_id] = event
            return event
        row = {"event_id": event.event_id, "workspace_id": event.workspace_id, "run_id": event.run_id,
               "event_type": event.event_type, "actor_type": event.actor_type, "actor_id": event.actor_id,
               "payload_json": dict(event.payload), "occurred_at": event.occurred_at, "fingerprint": event.fingerprint}
        try:
            await asyncio.to_thread(lambda: db.table("agent_workforce_events").insert(row).execute())
        except Exception as exc:
            raise RuntimeError(f"{ControlBlock.DURABLE_STORE_UNAVAILABLE.value}: {exc}") from exc
        return event

    async def events(self, run_id: str, *, limit: int = 100) -> tuple[WorkforceEvent, ...]:
        db = self._require_store()
        if db is None:
            return tuple(e for e in self._events.values() if e.run_id == str(run_id))[-limit:]
        result = await asyncio.to_thread(lambda: db.table("agent_workforce_events").select("*").eq("workspace_id", self.workspace_id).eq("run_id", str(run_id)).order("occurred_at", desc=False).limit(limit).execute())
        out = []
        for row in (getattr(result, "data", None) or []):
            payload = row.get("payload_json") or {}
            e = WorkforceEvent(str(row["event_id"]), str(row["workspace_id"]), str(row["run_id"]), str(row["event_type"]),
                               str(row["actor_type"]), str(row["actor_id"]), payload, str(row["occurred_at"]), str(row["fingerprint"]))
            if e.fingerprint != e.calculate_fingerprint():
                raise ValueError("workforce_event_tampered")
            out.append(e)
        return tuple(out)


def _parse_run(row: Mapping[str, Any]) -> AutonomousRun:
    event = row.get("event_json") or {}; summary = row.get("result_summary_json") or {}
    if isinstance(event, str): event = json.loads(event or "{}")
    if isinstance(summary, str): summary = json.loads(summary or "{}")
    return AutonomousRun(
        run_id=str(row["run_id"]), workspace_id=str(row["workspace_id"]), agent_id=str(row["agent_id"]),
        plan_id=str(row["plan_id"]), objective=str(row["objective"]), workflow_id=str(row["workflow_id"]),
        trigger=str(row["trigger"]), event=event, budget_id=row.get("budget_id"),
        coordination_id=row.get("coordination_id"), takeover_case_id=row.get("takeover_case_id"),
        status=AutonomousRunStatus(str(row["status"])), disposition=AutonomousRunDisposition(str(row["disposition"])),
        version=str(row.get("version") or "1"), result_summary=summary, run_fingerprint=str(row["run_fingerprint"]),
    )


__all__ = ["ControlCommand", "ControlBlock", "WorkforceEvent", "WorkforceSnapshot", "WorkforceObservabilityControlPlane"]
