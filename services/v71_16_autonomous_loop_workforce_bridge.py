"""V71.16 — Canonical Autonomous Loop -> Governed Workforce bridge.

The legacy AutonomousLoop remains an intelligence producer. This bridge makes its
business proposals enter the V71 workforce model instead of calling the legacy
pipeline directly. Missing workforce identity/plan/workflow configuration is an
explicit advisory outcome, never an invented execution path.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional
import hashlib
import json
import logging
import uuid

from services.v71_9_bounded_autonomous_operations import (
    BoundedAutonomousOperationsRegistry,
)

logger = logging.getLogger("kanyoza.v71.16")


@dataclass(frozen=True)
class WorkforceProposal:
    proposal_id: str
    workspace_id: str
    source: str
    capability: str
    disposition: str
    execution_requested: bool
    authority_granted: bool
    run_id: Optional[str] = None
    reason: str = ""
    fingerprint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "workspace_id": self.workspace_id,
            "source": self.source,
            "capability": self.capability,
            "disposition": self.disposition,
            "execution_requested": self.execution_requested,
            "authority_granted": self.authority_granted,
            "run_id": self.run_id,
            "reason": self.reason,
            "fingerprint": self.fingerprint,
        }


class AutonomousLoopWorkforceBridge:
    VERSION = "v71.16"

    def __init__(self, *, production_runtime: Any, db: Any = None):
        if production_runtime is None:
            raise ValueError("production runtime is required")
        self.production_runtime = production_runtime
        self.db = db
        self._proposals: dict[str, WorkforceProposal] = {}

    @staticmethod
    def _proposal_id(payload: Mapping[str, Any], capability: str) -> str:
        material = {
            "event_id": str(payload.get("event_id") or ""),
            "correlation_id": str(payload.get("correlation_id") or ""),
            "workspace_id": str(payload.get("workspace_id") or ""),
            "capability": capability,
            "source": "autonomous_loop",
        }
        digest = hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]
        return f"alp_{digest}"

    def _advisory(self, *, proposal_id: str, workspace_id: str, capability: str, reason: str) -> WorkforceProposal:
        proposal = WorkforceProposal(
            proposal_id=proposal_id,
            workspace_id=workspace_id,
            source="autonomous_loop",
            capability=capability,
            disposition="ADVISORY_ONLY",
            execution_requested=False,
            authority_granted=False,
            reason=reason,
        )
        proposal = WorkforceProposal(**{**proposal.__dict__, "fingerprint": self._fingerprint(proposal)})
        self._proposals[proposal_id] = proposal
        return proposal

    @staticmethod
    def _fingerprint(proposal: WorkforceProposal) -> str:
        material = {k: v for k, v in proposal.__dict__.items() if k != "fingerprint"}
        return hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    async def propose(self, payload: Mapping[str, Any], *, capability: str, workflow: Optional[Mapping[str, Any]] = None) -> WorkforceProposal:
        workspace_id = str(payload.get("workspace_id") or "").strip()
        capability = str(capability or "").strip()
        proposal_id = self._proposal_id(payload, capability)
        if not workspace_id:
            return self._advisory(proposal_id=proposal_id, workspace_id="", capability=capability,
                                  reason="workspace_id_required")
        if not capability:
            return self._advisory(proposal_id=proposal_id, workspace_id=workspace_id, capability="unknown",
                                  reason="capability_required")

        agent_id = str(payload.get("agent_id") or "").strip()
        plan_id = str(payload.get("plan_id") or "").strip()
        workflow_id = str(payload.get("workflow_id") or (workflow or {}).get("id") or "").strip()
        if not agent_id or not plan_id or not workflow_id:
            return self._advisory(
                proposal_id=proposal_id,
                workspace_id=workspace_id,
                capability=capability,
                reason="workforce_identity_plan_workflow_not_configured",
            )

        runtime = self.production_runtime.workspace_runtime(workspace_id)
        operations: BoundedAutonomousOperationsRegistry = runtime["bounded_autonomy"]
        durable = runtime["durable_runtime"]

        run_id = str(payload.get("run_id") or f"ar_{uuid.uuid4().hex}")
        event = dict(payload)
        event["source"] = "autonomous_loop"
        event["proposal_id"] = proposal_id
        event["capability"] = capability

        try:
            run = operations.create(
                run_id=run_id,
                agent_id=agent_id,
                plan_id=plan_id,
                objective=str(payload.get("objective") or capability),
                workflow_id=workflow_id,
                trigger=str(payload.get("trigger") or "autonomous_loop"),
                event=event,
                budget_id=payload.get("budget_id"),
                coordination_id=payload.get("coordination_id"),
                takeover_case_id=payload.get("takeover_case_id"),
            )
            snapshot = await durable.persist(run)
            proposal = WorkforceProposal(
                proposal_id=proposal_id,
                workspace_id=workspace_id,
                source="autonomous_loop",
                capability=capability,
                disposition="EXECUTION_CANDIDATE",
                execution_requested=False,
                authority_granted=False,
                run_id=snapshot.run.run_id,
                reason="admitted_to_durable_workforce_for_governance",
            )
            proposal = WorkforceProposal(**{**proposal.__dict__, "fingerprint": self._fingerprint(proposal)})
            self._proposals[proposal_id] = proposal
            logger.info("V71.16 workforce proposal created run=%s capability=%s workspace=%s", run_id, capability, workspace_id)
            return proposal
        except Exception as exc:
            logger.warning("V71.16 workforce proposal blocked: %s", exc)
            return self._advisory(
                proposal_id=proposal_id,
                workspace_id=workspace_id,
                capability=capability,
                reason=f"workforce_admission_blocked:{type(exc).__name__}",
            )

    def get(self, proposal_id: str) -> Optional[WorkforceProposal]:
        return self._proposals.get(str(proposal_id))

    def status(self) -> dict[str, Any]:
        return {
            "version": self.VERSION,
            "canonical": True,
            "source": "AutonomousLoop",
            "role": ["observe", "interpret", "propose"],
            "execution_requested_by_bridge": False,
            "authority_granted_by_bridge": False,
            "canonical_path": "AutonomousLoop -> workforce proposal -> V71 identity/plan -> governance -> Action Kernel -> durable outbox",
            "parallel_execution_plane": False,
            "proposals_in_process": len(self._proposals),
        }


def install_autonomous_loop_workforce_bridge(app: Any, container: Any) -> AutonomousLoopWorkforceBridge:
    production_runtime = getattr(app.state, "production_runtime", None)
    if production_runtime is None:
        raise RuntimeError("V71.13 production runtime must be installed first")
    bridge = AutonomousLoopWorkforceBridge(production_runtime=production_runtime)
    app.state.autonomous_loop_workforce_bridge = bridge
    try:
        from core.autonomous_loop import AutonomousLoop
        loop = container.try_resolve(AutonomousLoop)
        if loop is not None and hasattr(loop, "set_workforce_bridge"):
            loop.set_workforce_bridge(bridge)
    except Exception as exc:
        logger.warning("V71.16 AutonomousLoop bridge deferred: %s", exc)
    logger.info("V71.16 AutonomousLoop -> Governed Workforce bridge installed")
    return bridge
