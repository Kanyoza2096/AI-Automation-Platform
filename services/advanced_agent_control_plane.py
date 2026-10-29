"""Phase 6 — Advanced Agent Control Plane convergence.

Read-only convergence over KDO's existing governed agent/workforce primitives.
This module does not replace identity, permissions, planning, evaluation,
budgets, takeover, coordination, durable runtime, or governance.  It provides
one authoritative *control-plane view* of their state for reasoning and operator
surfaces while preserving each subsystem's authority boundary.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional
import hashlib
import json


class ControlPlaneState(str, Enum):
    READY = "ready"
    DEGRADED = "degraded"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class AgentControlSnapshot:
    workspace_id: str
    agent_id: str
    identity: Mapping[str, Any]
    permissions: tuple[Mapping[str, Any], ...] = ()
    plans: tuple[Mapping[str, Any], ...] = ()
    evaluations: tuple[Mapping[str, Any], ...] = ()
    budgets: tuple[Mapping[str, Any], ...] = ()
    takeover_cases: tuple[Mapping[str, Any], ...] = ()
    coordination_requests: tuple[Mapping[str, Any], ...] = ()
    runtime: Mapping[str, Any] = field(default_factory=dict)
    state: ControlPlaneState = ControlPlaneState.UNKNOWN
    blocks: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    authority_granted: bool = False
    execution_requested: bool = False
    snapshot_fingerprint: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "agent_id": self.agent_id,
            "identity": dict(self.identity),
            "permissions": [dict(x) for x in self.permissions],
            "plans": [dict(x) for x in self.plans],
            "evaluations": [dict(x) for x in self.evaluations],
            "budgets": [dict(x) for x in self.budgets],
            "takeover_cases": [dict(x) for x in self.takeover_cases],
            "coordination_requests": [dict(x) for x in self.coordination_requests],
            "runtime": dict(self.runtime),
            "state": self.state.value,
            "blocks": list(self.blocks),
            "warnings": list(self.warnings),
            "authority_granted": False,
            "execution_requested": False,
        }

    def fingerprint(self) -> str:
        raw = json.dumps(self.canonical_payload(), sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()


class AdvancedAgentControlPlane:
    """Canonical composition boundary for the existing agent control planes."""

    VERSION = "phase6-v1"

    def __init__(self, workspace_id: str, *, identity: Any, permissions: Any,
                 planning: Any = None, evaluation: Any = None, budgets: Any = None,
                 takeover: Any = None, coordination: Any = None, runtime: Any = None):
        ws = str(workspace_id or "").strip()
        if not ws:
            raise ValueError("workspace_id is required")
        self.workspace_id = ws
        self.identity = identity
        self.permissions = permissions
        self.planning = planning
        self.evaluation = evaluation
        self.budgets = budgets
        self.takeover = takeover
        self.coordination = coordination
        self.runtime = runtime
        for component in (identity, permissions, planning, evaluation, budgets, takeover, coordination):
            if component is not None and getattr(component, "workspace_id", ws) != ws:
                raise ValueError("workspace_mismatch")

    @staticmethod
    def _mapping(value: Any) -> dict[str, Any]:
        if hasattr(value, "__dict__"):
            return dict(value.__dict__)
        if isinstance(value, Mapping):
            return dict(value)
        return {"value": value}

    def _agent(self, agent_id: str) -> Any:
        agent = self.identity.get(str(agent_id)) if self.identity is not None else None
        if agent is None:
            raise ValueError("agent_not_found")
        if hasattr(self.identity, "assert_integrity"):
            self.identity.assert_integrity(agent)
        if getattr(agent, "workspace_id", self.workspace_id) != self.workspace_id:
            raise ValueError("workspace_mismatch")
        return agent

    def snapshot(self, *, agent_id: str) -> AgentControlSnapshot:
        agent = self._agent(agent_id)
        aid = str(agent_id)
        blocks: list[str] = []
        warnings: list[str] = []
        identity = self._mapping(agent)

        def collect(registry: Any, method: str, label: str) -> tuple[dict[str, Any], ...]:
            if registry is None:
                return ()
            try:
                values = getattr(registry, method)(aid)
                return tuple(self._mapping(v) for v in values)
            except TypeError:
                try:
                    values = getattr(registry, method)(agent_id=aid)
                    return tuple(self._mapping(v) for v in values)
                except Exception as exc:
                    warnings.append(f"{label}:{type(exc).__name__}")
            except Exception as exc:
                warnings.append(f"{label}:{type(exc).__name__}")
            return ()

        permissions = collect(self.permissions, "list_permissions", "permissions")
        plans = collect(self.planning, "list_plans", "plans")
        evaluations = collect(self.evaluation, "list_evaluations", "evaluations")
        budgets: tuple[dict[str, Any], ...] = ()
        if self.budgets is not None:
            try:
                summary = self.budgets.summary(aid)
                budgets = (self._mapping(summary),)
            except Exception as exc:
                warnings.append(f"budgets:{type(exc).__name__}")
        takeover = collect(self.takeover, "pending", "takeover")

        coordination: tuple[dict[str, Any], ...] = ()
        if self.coordination is not None:
            try:
                values = self.coordination.list_requests(agent_id=aid)
                coordination = tuple(self._mapping(v) for v in values)
            except TypeError:
                try:
                    values = self.coordination.list_requests(aid)
                    coordination = tuple(self._mapping(v) for v in values)
                except Exception as exc:
                    warnings.append(f"coordination:{type(exc).__name__}")
            except Exception as exc:
                warnings.append(f"coordination:{type(exc).__name__}")

        runtime_data: dict[str, Any] = {}
        if self.runtime is not None:
            try:
                status = self.runtime.status() if callable(getattr(self.runtime, "status", None)) else None
                if status is not None:
                    runtime_data = self._mapping(status)
            except Exception as exc:
                warnings.append(f"runtime:{type(exc).__name__}")

        status_value = getattr(agent, "status", None)
        status_text = getattr(status_value, "value", str(status_value or ""))
        if status_text == "retired":
            blocks.append("agent_retired")
        elif status_text != "active":
            blocks.append("agent_not_active")
        if blocks:
            state = ControlPlaneState.BLOCKED
        elif warnings:
            state = ControlPlaneState.DEGRADED
        else:
            state = ControlPlaneState.READY

        result = AgentControlSnapshot(
            workspace_id=self.workspace_id, agent_id=aid, identity=identity,
            permissions=tuple(sorted(permissions, key=lambda x: str(x.get("permission_id", "")))),
            plans=tuple(sorted(plans, key=lambda x: str(x.get("plan_id", "")))),
            evaluations=tuple(sorted(evaluations, key=lambda x: str(x.get("evaluation_id", "")))),
            budgets=tuple(sorted(budgets, key=lambda x: str(x.get("budget_id", "")))),
            takeover_cases=tuple(sorted(takeover, key=lambda x: str(x.get("case_id", "")))),
            coordination_requests=tuple(sorted(coordination, key=lambda x: str(x.get("coordination_id", "")))),
            runtime=runtime_data, state=state, blocks=tuple(sorted(set(blocks))),
            warnings=tuple(sorted(set(warnings))), authority_granted=False, execution_requested=False,
        )
        return AgentControlSnapshot(**{**result.__dict__, "snapshot_fingerprint": result.fingerprint()})

    @classmethod
    def context(cls, snapshot: AgentControlSnapshot) -> dict[str, Any]:
        if snapshot.snapshot_fingerprint != snapshot.fingerprint():
            raise ValueError("tampered_snapshot")
        return {
            "version": cls.VERSION,
            "workspace_id": snapshot.workspace_id,
            "agent_id": snapshot.agent_id,
            "state": snapshot.state.value,
            "blocks": list(snapshot.blocks),
            "warnings": list(snapshot.warnings),
            "snapshot_fingerprint": snapshot.snapshot_fingerprint,
            "authority": "none",
            "authority_granted": False,
            "execution_requested": False,
            "read_only": True,
        }


__all__ = ["ControlPlaneState", "AgentControlSnapshot", "AdvancedAgentControlPlane"]
