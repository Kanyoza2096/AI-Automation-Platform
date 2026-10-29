"""V71.6 — Governed AI Workforce: Cost & Risk Budgets.

Budgets bound agent activity; they never grant authority. A budget check is an
admission signal that must still pass KDO governance, Action Kernel and execution
controls. No provider or side-effect calls live here.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional
import hashlib, json
import asyncio
import uuid

from services.v71_1_agent_identity import AgentIdentityRegistry, AgentStatus


class BudgetStatus(str, Enum):
    ACTIVE = "active"
    EXHAUSTED = "exhausted"
    SUSPENDED = "suspended"
    EXPIRED = "expired"


class BudgetBlock(str, Enum):
    TENANT_MISMATCH = "tenant_mismatch"
    AGENT_NOT_FOUND = "agent_not_found"
    AGENT_NOT_ACTIVE = "agent_not_active"
    DUPLICATE_BUDGET = "duplicate_budget"
    BUDGET_NOT_FOUND = "budget_not_found"
    BUDGET_TAMPERED = "budget_tampered"
    INVALID_LIMIT = "invalid_limit"
    INVALID_USAGE = "invalid_usage"
    EXHAUSTED = "budget_exhausted"
    SUSPENDED = "budget_suspended"
    EXPIRED = "budget_expired"
    INSUFFICIENT_BUDGET = "insufficient_budget"
    NEGATIVE_DELTA = "negative_delta"


@dataclass(frozen=True)
class AgentBudget:
    budget_id: str
    workspace_id: str
    agent_id: str
    max_actions: Optional[int] = None
    max_cost: Optional[float] = None
    max_risk_units: Optional[float] = None
    status: BudgetStatus = BudgetStatus.ACTIVE
    version: str = "1"
    metadata: Mapping[str, Any] = field(default_factory=dict)
    used_actions: int = 0
    used_cost: float = 0.0
    used_risk_units: float = 0.0
    budget_fingerprint: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "budget_id": self.budget_id, "workspace_id": self.workspace_id,
            "agent_id": self.agent_id, "max_actions": self.max_actions,
            "max_cost": self.max_cost, "max_risk_units": self.max_risk_units,
            "status": self.status.value, "version": self.version,
            "metadata": dict(self.metadata), "used_actions": self.used_actions,
            "used_cost": round(self.used_cost, 6),
            "used_risk_units": round(self.used_risk_units, 6),
        }

    def fingerprint(self) -> str:
        raw = json.dumps(self.canonical_payload(), sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()

    def remaining(self) -> Mapping[str, Optional[float]]:
        return {
            "actions": None if self.max_actions is None else max(0, self.max_actions - self.used_actions),
            "cost": None if self.max_cost is None else round(max(0.0, self.max_cost - self.used_cost), 6),
            "risk_units": None if self.max_risk_units is None else round(max(0.0, self.max_risk_units - self.used_risk_units), 6),
        }


@dataclass(frozen=True)
class BudgetCheck:
    allowed: bool
    budget_id: str
    reasons: tuple[str, ...] = ()
    remaining: Mapping[str, Optional[float]] = field(default_factory=dict)
    authority_granted: bool = False


class AgentBudgetRegistry:
    VERSION = "v71.6"

    def __init__(self, workspace_id: str, identity_registry: AgentIdentityRegistry):
        ws = str(workspace_id or "").strip()
        if not ws or identity_registry.workspace_id != ws:
            raise ValueError(BudgetBlock.TENANT_MISMATCH.value)
        self.workspace_id = ws
        self.identity_registry = identity_registry
        self._budgets: dict[str, AgentBudget] = {}

    def _validate_agent(self, agent_id: str):
        agent = self.identity_registry.get(agent_id)
        if agent is None:
            raise ValueError(BudgetBlock.AGENT_NOT_FOUND.value)
        self.identity_registry.assert_integrity(agent)
        if agent.status != AgentStatus.ACTIVE:
            raise ValueError(BudgetBlock.AGENT_NOT_ACTIVE.value)
        return agent

    @staticmethod
    def _limit(value: Any, *, integer: bool = False):
        if value is None:
            return None
        try:
            result = int(value) if integer else float(value)
        except (TypeError, ValueError):
            raise ValueError(BudgetBlock.INVALID_LIMIT.value)
        if result < 0 or (not integer and result != result):
            raise ValueError(BudgetBlock.INVALID_LIMIT.value)
        return result

    def create(self, *, budget_id: str, agent_id: str,
               max_actions: Optional[int] = None,
               max_cost: Optional[float] = None,
               max_risk_units: Optional[float] = None,
               version: str = "1", metadata: Optional[Mapping[str, Any]] = None) -> AgentBudget:
        bid = str(budget_id or "").strip()
        if not bid or bid in self._budgets:
            raise ValueError(BudgetBlock.DUPLICATE_BUDGET.value)
        self._validate_agent(agent_id)
        if not str(version or "").strip():
            raise ValueError(BudgetBlock.INVALID_LIMIT.value)
        limits = (self._limit(max_actions, integer=True), self._limit(max_cost), self._limit(max_risk_units))
        if all(v is None for v in limits):
            raise ValueError(BudgetBlock.INVALID_LIMIT.value)
        budget = AgentBudget(bid, self.workspace_id, str(agent_id), limits[0], limits[1], limits[2],
                             BudgetStatus.ACTIVE, str(version), dict(metadata or {}), 0, 0.0, 0.0, "")
        budget = AgentBudget(**{**budget.__dict__, "budget_fingerprint": budget.fingerprint()})
        self._budgets[bid] = budget
        return budget

    def get(self, budget_id: str) -> Optional[AgentBudget]:
        return self._budgets.get(str(budget_id))

    def assert_integrity(self, budget: AgentBudget) -> None:
        if budget.workspace_id != self.workspace_id:
            raise ValueError(BudgetBlock.TENANT_MISMATCH.value)
        if budget.budget_fingerprint != budget.fingerprint():
            raise ValueError(BudgetBlock.BUDGET_TAMPERED.value)

    def check(self, budget_id: str, *, actions: int = 0, cost: float = 0.0, risk_units: float = 0.0) -> BudgetCheck:
        budget = self.get(budget_id)
        if budget is None:
            raise ValueError(BudgetBlock.BUDGET_NOT_FOUND.value)
        self.assert_integrity(budget)
        self._validate_agent(budget.agent_id)
        try:
            a, c, r = int(actions), float(cost), float(risk_units)
        except (TypeError, ValueError):
            raise ValueError(BudgetBlock.INVALID_USAGE.value)
        if a < 0 or c < 0 or r < 0:
            raise ValueError(BudgetBlock.NEGATIVE_DELTA.value)
        reasons = []
        if budget.status == BudgetStatus.EXHAUSTED: reasons.append(BudgetBlock.EXHAUSTED.value)
        if budget.status == BudgetStatus.SUSPENDED: reasons.append(BudgetBlock.SUSPENDED.value)
        if budget.status == BudgetStatus.EXPIRED: reasons.append(BudgetBlock.EXPIRED.value)
        if budget.max_actions is not None and budget.used_actions + a > budget.max_actions: reasons.append(BudgetBlock.INSUFFICIENT_BUDGET.value + ":actions")
        if budget.max_cost is not None and budget.used_cost + c > budget.max_cost: reasons.append(BudgetBlock.INSUFFICIENT_BUDGET.value + ":cost")
        if budget.max_risk_units is not None and budget.used_risk_units + r > budget.max_risk_units: reasons.append(BudgetBlock.INSUFFICIENT_BUDGET.value + ":risk")
        return BudgetCheck(not reasons, budget.budget_id, tuple(reasons), budget.remaining(), False)

    def record_usage(self, budget_id: str, *, actions: int = 0, cost: float = 0.0, risk_units: float = 0.0) -> AgentBudget:
        budget = self.get(budget_id)
        if budget is None:
            raise ValueError(BudgetBlock.BUDGET_NOT_FOUND.value)
        self.assert_integrity(budget)
        check = self.check(budget_id, actions=actions, cost=cost, risk_units=risk_units)
        if not check.allowed:
            raise ValueError(check.reasons[0])
        updated = AgentBudget(**{**budget.__dict__,
            "used_actions": budget.used_actions + int(actions),
            "used_cost": round(budget.used_cost + float(cost), 6),
            "used_risk_units": round(budget.used_risk_units + float(risk_units), 6),
            "budget_fingerprint": ""})
        exhausted = ((updated.max_actions is not None and updated.used_actions >= updated.max_actions) or
                     (updated.max_cost is not None and updated.used_cost >= updated.max_cost) or
                     (updated.max_risk_units is not None and updated.used_risk_units >= updated.max_risk_units))
        if exhausted: updated = AgentBudget(**{**updated.__dict__, "status": BudgetStatus.EXHAUSTED})
        updated = AgentBudget(**{**updated.__dict__, "budget_fingerprint": updated.fingerprint()})
        self._budgets[budget_id] = updated
        return updated

    def set_status(self, budget_id: str, status: BudgetStatus) -> AgentBudget:
        budget = self.get(budget_id)
        if budget is None: raise ValueError(BudgetBlock.BUDGET_NOT_FOUND.value)
        self.assert_integrity(budget)
        updated = AgentBudget(**{**budget.__dict__, "status": BudgetStatus(status), "budget_fingerprint": ""})
        updated = AgentBudget(**{**updated.__dict__, "budget_fingerprint": updated.fingerprint()})
        self._budgets[budget_id] = updated
        return updated

    def summary(self, agent_id: str) -> Mapping[str, Any]:
        self._validate_agent(agent_id)
        rows = [b for b in self._budgets.values() if b.agent_id == str(agent_id)]
        return {"agent_id": str(agent_id), "budget_count": len(rows),
                "active_count": sum(b.status == BudgetStatus.ACTIVE for b in rows),
                "exhausted_count": sum(b.status == BudgetStatus.EXHAUSTED for b in rows),
                "advisory": True, "authority_granted": False}



@dataclass(frozen=True)
class BudgetReservation:
    reservation_id: str
    workspace_id: str
    budget_id: str
    run_id: str
    actions: int
    cost: float
    risk_units: float
    status: str
    claim_token: str


class DurableAgentBudgetReservations:
    """Atomic distributed budget reservation facade. Never grants authority."""
    def __init__(self, *, db: Any, workspace_id: str, lease_seconds: int = 120):
        self.db = db
        self.workspace_id = str(workspace_id)
        self.lease_seconds = max(1, int(lease_seconds))

    async def reserve(self, *, budget_id: str, run_id: str, actions: int = 0, cost: float = 0.0, risk_units: float = 0.0, reservation_id: Optional[str] = None, claim_token: Optional[str] = None) -> BudgetReservation:
        rid = reservation_id or f"br_{uuid.uuid4().hex}"
        token = claim_token or uuid.uuid4().hex
        result = await asyncio.to_thread(lambda: self.db.rpc("reserve_agent_budget", {
            "p_workspace_id": self.workspace_id, "p_budget_id": str(budget_id), "p_run_id": str(run_id),
            "p_reservation_id": rid, "p_claim_token": token, "p_lease_seconds": self.lease_seconds,
            "p_actions": int(actions), "p_cost": float(cost), "p_risk_units": float(risk_units),
        }).execute())
        rows = getattr(result, "data", None) or []
        if not rows: raise ValueError(BudgetBlock.INSUFFICIENT_BUDGET.value)
        row = rows[0]
        return BudgetReservation(str(row["reservation_id"]), self.workspace_id, str(row["budget_id"]), str(row["run_id"]), int(row["actions"]), float(row["cost"]), float(row["risk_units"]), str(row["status"]), str(row["claim_token"]))

    async def finalize(self, reservation: BudgetReservation, *, commit: bool) -> BudgetReservation:
        result = await asyncio.to_thread(lambda: self.db.rpc("finalize_agent_budget_reservation", {
            "p_workspace_id": self.workspace_id, "p_reservation_id": reservation.reservation_id,
            "p_claim_token": reservation.claim_token, "p_commit": bool(commit),
        }).execute())
        rows = getattr(result, "data", None) or []
        if not rows: raise ValueError("budget_reservation_not_found_or_claim_lost")
        row = rows[0]
        return BudgetReservation(str(row["reservation_id"]), self.workspace_id, str(row["budget_id"]), str(row["run_id"]), int(row["actions"]), float(row["cost"]), float(row["risk_units"]), str(row["status"]), str(row["claim_token"]))

    async def recover_expired(self, *, limit: int = 100) -> int:
        result = await asyncio.to_thread(lambda: self.db.rpc("recover_agent_budget_reservations", {"p_limit": int(max(1, limit))}).execute())
        return int(getattr(result, "data", 0) or 0)


__all__ = ["BudgetStatus", "BudgetBlock", "AgentBudget", "BudgetCheck", "AgentBudgetRegistry", "BudgetReservation", "DurableAgentBudgetReservations"]
