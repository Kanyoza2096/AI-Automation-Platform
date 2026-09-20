"""V71.17 — Durable Workforce Execution Convergence.

Turns a durable V71 autonomous run into an execution candidate without creating
another executor. The coordinator claims V71.10 state, resolves only the
existing business-workflow catalog, reconstructs the advisory autonomy envelope
from the immutable run event, and delegates consequential work to V71.9's
existing governed workflow path. External side effects remain downstream of
BusinessAutomation -> Action Kernel -> durable outbox -> connector/provider.

Safety rule: a durable run without a complete governance manifest is blocked;
workers never invent an envelope, workflow, approval, or authority.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Optional
import asyncio
import logging

from services.business_autonomy_envelope import (
    BusinessAutonomyEnvelope, SuccessMetric, RiskBoundary, ResourceLimit,
    EscalationTrigger, AutonomyMode, EnvelopeState,
)
from services.business_automation_catalog import get_template
from services.v71_9_bounded_autonomous_operations import AutonomousRunStatus, AutonomousRunDisposition
from services.v71_10_durable_workforce_runtime import DurableAutonomousWorkforceRuntime, RuntimeLease

logger = logging.getLogger("kanyoza.v71.17")


class WorkforceExecutionBlock(str, Enum):
    GOVERNANCE_MANIFEST_REQUIRED = "governance_manifest_required"
    GOVERNANCE_MANIFEST_INVALID = "governance_manifest_invalid"
    WORKFLOW_UNAVAILABLE = "workflow_unavailable"
    LEASE_REQUIRED = "lease_required"
    RECONCILIATION_REQUIRED = "reconciliation_required"


@dataclass(frozen=True)
class WorkforceExecutionResult:
    run_id: str
    workspace_id: str
    status: str
    disposition: str
    executed: bool
    authoritative: bool
    reason: str = ""
    execution_result: Any = None

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _envelope_from_manifest(manifest: Mapping[str, Any], workspace_id: str) -> BusinessAutonomyEnvelope:
    try:
        metrics = tuple(SuccessMetric(
            name=str(m["name"]), operator=str(m["operator"]), target=float(m["target"]),
            unit=str(m.get("unit") or ""), minimum_confidence=float(m.get("minimum_confidence", 0.0)),
        ) for m in manifest.get("success_metrics", ()))
        risk = manifest.get("risk_boundary") or {}
        limits = tuple(ResourceLimit(str(r["key"]), float(r["limit"]), str(r.get("period") or "lifetime"))
                       for r in manifest.get("resource_limits", ()))
        return BusinessAutonomyEnvelope(
            workspace_id=str(manifest.get("workspace_id") or workspace_id),
            objective=str(manifest["objective"]), success_metrics=metrics,
            allowed_actions=tuple(str(x) for x in manifest.get("allowed_actions", ())),
            scope=manifest.get("scope") or {},
            risk_boundary=RiskBoundary(
                max_risk=str(risk.get("max_risk") or "medium"),
                require_approval_for=tuple(str(x) for x in risk.get("require_approval_for", ("high", "critical"))),
                prohibited_actions=tuple(str(x) for x in risk.get("prohibited_actions", ())),
            ),
            resource_limits=limits,
            escalation_triggers=tuple(EscalationTrigger(str(x)) for x in manifest.get("escalation_triggers", ())),
            mode=AutonomyMode(str(manifest.get("mode") or "suggest")),
            valid_from=_dt(manifest["valid_from"]),
            valid_until=_dt(manifest["valid_until"]) if manifest.get("valid_until") else None,
            state=EnvelopeState(str(manifest.get("state") or "draft")),
            goal_id=manifest.get("goal_id"), envelope_id=str(manifest.get("envelope_id") or ""),
            version=int(manifest.get("version", 1)),
        )
    except Exception as exc:
        raise ValueError(WorkforceExecutionBlock.GOVERNANCE_MANIFEST_INVALID.value) from exc


class DurableWorkforceExecutionCoordinator:
    VERSION = "v71.17"

    def __init__(self, *, production_runtime: Any, workspace_id: str):
        if production_runtime is None:
            raise ValueError("production runtime is required")
        self.production_runtime = production_runtime
        self.workspace_id = str(workspace_id or "").strip()
        if not self.workspace_id:
            raise ValueError("workspace_id is required")

    def _runtime(self) -> Mapping[str, Any]:
        return self.production_runtime.workspace_runtime(self.workspace_id)

    def _workflow(self, workflow_id: str) -> Mapping[str, Any]:
        workflow = get_template(str(workflow_id))
        if not workflow:
            raise ValueError(WorkforceExecutionBlock.WORKFLOW_UNAVAILABLE.value)
        return workflow

    @staticmethod
    def _manifest(run: Any) -> Mapping[str, Any]:
        event = dict(run.event or {})
        manifest = event.get("_kdo_governance_envelope")
        if not manifest:
            raise ValueError(WorkforceExecutionBlock.GOVERNANCE_MANIFEST_REQUIRED.value)
        if not isinstance(manifest, Mapping):
            raise ValueError(WorkforceExecutionBlock.GOVERNANCE_MANIFEST_INVALID.value)
        return manifest

    async def execute_claimed(self, lease: RuntimeLease, *, approved: bool = False,
                              budget_actions: int = 1, budget_cost: float = 0.0,
                              budget_risk_units: float = 0.0) -> WorkforceExecutionResult:
        if lease.workspace_id != self.workspace_id:
            raise ValueError("tenant_mismatch")
        runtime = self._runtime()
        durable: DurableAutonomousWorkforceRuntime = runtime["durable_runtime"]
        snap = await durable.get(lease.run_id)
        if snap.reconciliation_required or snap.run.status != AutonomousRunStatus.RUNNING:
            return WorkforceExecutionResult(lease.run_id, self.workspace_id, snap.run.status.value,
                                            snap.run.disposition.value, False, snap.authoritative,
                                            WorkforceExecutionBlock.RECONCILIATION_REQUIRED.value)
        try:
            manifest = self._manifest(snap.run)
            envelope = _envelope_from_manifest(manifest, self.workspace_id)
            if envelope.fingerprint() != str(manifest.get("fingerprint") or envelope.fingerprint()):
                raise ValueError(WorkforceExecutionBlock.GOVERNANCE_MANIFEST_INVALID.value)
            workflow = self._workflow(snap.run.workflow_id)
            engine = runtime["business_operating_layer"].engine
            result = await durable.execute_claimed(
                lease, envelope=envelope, workflow=workflow, engine=engine, approved=approved,
                budget_actions=budget_actions, budget_cost=budget_cost, budget_risk_units=budget_risk_units,
            )
            return WorkforceExecutionResult(lease.run_id, self.workspace_id, "completed",
                                            AutonomousRunDisposition.EXECUTION_CANDIDATE.value,
                                            True, True, "governed_workflow_completed", result)
        except ValueError as exc:
            try:
                await durable.finalize(lease, status=AutonomousRunStatus.BLOCKED,
                                       disposition=AutonomousRunDisposition.BLOCKED,
                                       result_summary={"reason": str(exc)})
            except Exception:
                logger.exception("failed to finalize blocked workforce run=%s", lease.run_id)
            return WorkforceExecutionResult(lease.run_id, self.workspace_id, "blocked",
                                            AutonomousRunDisposition.BLOCKED.value, False, True, str(exc))

    async def execute_run(self, run_id: str, *, lease_seconds: int = 300, approved: bool = False) -> WorkforceExecutionResult:
        runtime = self._runtime()
        durable: DurableAutonomousWorkforceRuntime = runtime["durable_runtime"]
        snap = await durable.claim(str(run_id), lease_seconds=lease_seconds)
        if not snap.lease:
            raise ValueError(WorkforceExecutionBlock.LEASE_REQUIRED.value)
        return await self.execute_claimed(snap.lease, approved=approved)

    async def reconcile_stale(self, run_id: str) -> Mapping[str, Any]:
        runtime = self._runtime()
        durable: DurableAutonomousWorkforceRuntime = runtime["durable_runtime"]
        snap = await durable.mark_stale_for_reconciliation(str(run_id))
        return {
            "run_id": snap.run.run_id,
            "status": snap.run.status.value,
            "reconciliation_required": snap.reconciliation_required,
            "authoritative": snap.authoritative,
        }

    def status(self) -> dict[str, Any]:
        return {
            "version": self.VERSION,
            "workspace_id": self.workspace_id,
            "canonical": True,
            "execution_authority": "existing Action Kernel only",
            "durable_state": "V71.10",
            "workflow_source": "business_automation_catalog",
            "governance_source": "durable run event manifest + existing governance",
            "provider_execution_here": False,
            "parallel_execution_plane": False,
            "restart_policy": "claim durable run; never blindly reclaim expired RUNNING state; reconcile uncertainty first",
        }


def install_workforce_execution_coordinator(app: Any, container: Any) -> None:
    # Coordinator is workspace-scoped and therefore instantiated on demand by
    # routes/workers. This installation only exposes the factory at the app boundary.
    runtime = getattr(app.state, "production_runtime", None)
    if runtime is None:
        raise RuntimeError("V71.13 production runtime must be installed first")
    factory = lambda workspace_id: DurableWorkforceExecutionCoordinator(
        production_runtime=runtime, workspace_id=workspace_id
    )
    app.state.workforce_execution_coordinator_factory = factory
    try:
        from core.autonomous_loop import AutonomousLoop
        loop = container.try_resolve(AutonomousLoop)
        if loop is not None:
            loop._workforce_execution_coordinator_factory = factory
    except Exception as exc:
        logger.warning("V71.17 AutonomousLoop execution factory deferred: %s", exc)
    logger.info("V71.17 durable workforce execution coordinator installed")


__all__ = ["WorkforceExecutionBlock", "WorkforceExecutionResult", "DurableWorkforceExecutionCoordinator", "install_workforce_execution_coordinator"]
