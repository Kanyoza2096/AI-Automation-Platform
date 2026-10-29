"""V71.16 — Canonical Autonomous Loop -> Governed Workforce bridge.

The legacy AutonomousLoop remains an intelligence producer. This bridge makes its
business proposals enter the V71 workforce model instead of calling the legacy
pipeline directly. Missing workforce identity/plan/workflow configuration is an
explicit advisory outcome, never an invented execution path.

V71.16.1 (hardening) — Installation is now fail-loud:
  - Loop is resolved from the canonical singleton, not the container (the loop
    does not self-register with the container in the current lifecycle).
  - If the loop cannot be found or lacks set_workforce_bridge, the installer
    raises. Silent "installed" logs hiding missing wiring have been removed.
  - Successful installation logs the loop identity (id()) so runtime logs can
    be correlated against withhold warnings.
  - Resolver reads the module singleton synchronously. The previous
    asyncio.run_coroutine_threadsafe path could deadlock when called from
    inside the running event loop, silently timing out and returning None.
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


def _resolve_autonomous_loop(container: Any) -> Any:
    """
    Resolve the canonical AutonomousLoop instance.

    Order of preference:
      1. Direct read of the module-level singleton `core.autonomous_loop._autonomous_loop`.
         This is synchronous, cheap, and safe to call from any context — including
         from inside a running event loop, where the previous
         asyncio.run_coroutine_threadsafe(...).result() approach could deadlock
         and silently time out.
      2. Container resolution via `container.try_resolve(AutonomousLoop)` as a
         defensive fallback.

    Returns the loop instance, or None if neither path produced one.
    """
    # Path 1 — direct module attribute read
    try:
        import core.autonomous_loop as _al
        candidate = getattr(_al, "_autonomous_loop", None)
        if candidate is not None:
            return candidate
    except Exception as exc:
        logger.debug("V71.16 direct singleton read failed: %s", exc)

    # Path 2 — container lookup
    if container is not None:
        try:
            from core.autonomous_loop import AutonomousLoop
            candidate = container.try_resolve(AutonomousLoop)
            if candidate is not None:
                logger.info("V71.16 resolved AutonomousLoop via container (singleton was None)")
                return candidate
        except Exception as exc:
            logger.debug("V71.16 container resolve failed: %s", exc)

    return None


def install_autonomous_loop_workforce_bridge(app: Any, container: Any) -> AutonomousLoopWorkforceBridge:
    """
    Install the canonical V71.16 bridge onto the AutonomousLoop.

    Fails loudly if the loop cannot be resolved or lacks set_workforce_bridge.
    A silent deferral here produces a system that boots "cleanly" but never
    actually wires the workforce bridge — which is exactly the failure mode
    that V71.16.1 removes.

    MUST be called AFTER the AutonomousLoop exists. In the current lifecycle
    that means inside `lifecycle._boot_autonomous_loop`, after
    `init_autonomous_loop(...)` has returned.
    """
    production_runtime = getattr(app.state, "production_runtime", None)
    if production_runtime is None:
        raise RuntimeError("V71.13 production runtime must be installed first")

    bridge = AutonomousLoopWorkforceBridge(production_runtime=production_runtime)
    app.state.autonomous_loop_workforce_bridge = bridge

    loop = _resolve_autonomous_loop(container)

    if loop is None:
        raise RuntimeError(
            "V71.16 could not resolve AutonomousLoop from singleton or container"
        )

    if not hasattr(loop, "set_workforce_bridge"):
        raise RuntimeError(
            f"V71.16 resolved loop of type {type(loop).__name__} but it lacks "
            f"set_workforce_bridge; cannot attach workforce bridge"
        )

    loop.set_workforce_bridge(bridge)

    # Verify the installation actually took on the object we just resolved.
    # The attribute is private by convention, but the identity check is cheap
    # and catches any silent attribute-name drift.
    installed = getattr(loop, "_workforce_bridge", None)
    if installed is not bridge:
        raise RuntimeError(
            "V71.16 set_workforce_bridge did not take effect on the resolved loop"
        )

    logger.info(
        "V71.16 AutonomousLoop -> Governed Workforce bridge installed loop_id=%s bridge_id=%s",
        id(loop),
        id(bridge),
    )
    return bridge
