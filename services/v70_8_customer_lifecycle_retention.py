"""V70.8 — Customer Lifecycle & Retention Operating System.

A tenant-bound customer lifecycle state/read model that converts trusted customer
signals into explicit, governed workflow recommendations. It owns business state
and routing only; execution remains in BusinessOperatingLayer -> Action Kernel ->
durable outbox -> governed connector/provider.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional
import hashlib, json

from services.v70_business_operating_layer import BusinessOperatingLayer


class LifecycleStage(str, Enum):
    ONBOARDING = "onboarding"
    ACTIVE = "active"
    ADOPTION_RISK = "adoption_risk"
    RETENTION_RISK = "retention_risk"
    RECOVERY = "recovery"
    EXPANSION = "expansion"
    CHURNED = "churned"


class LifecycleBlock(str, Enum):
    TENANT_MISMATCH = "tenant_mismatch"
    MISSING_CUSTOMER_ID = "missing_customer_id"
    MISSING_EVENT_ID = "missing_event_id"
    DUPLICATE_EVENT = "duplicate_event"
    UNKNOWN_EVENT = "unknown_event"
    TERMINAL = "terminal"
    INVALID_SIGNAL = "invalid_signal"


EVENT_STAGE = {
    "customer.onboarding_started": LifecycleStage.ONBOARDING,
    "customer.activated": LifecycleStage.ACTIVE,
    "customer.adoption_risk": LifecycleStage.ADOPTION_RISK,
    "customer.retention_risk": LifecycleStage.RETENTION_RISK,
    "customer.recovery_started": LifecycleStage.RECOVERY,
    "customer.expansion_opportunity": LifecycleStage.EXPANSION,
    "customer.churned": LifecycleStage.CHURNED,
    "customer.recovered": LifecycleStage.ACTIVE,
}

# Explicit state transitions. A recovery/retention signal cannot silently rewind a
# verified churned customer; a new commercial lifecycle must be represented by a
# new trusted event/process rather than a state overwrite.
ALLOWED = {
    LifecycleStage.ONBOARDING: {LifecycleStage.ONBOARDING, LifecycleStage.ACTIVE, LifecycleStage.ADOPTION_RISK, LifecycleStage.CHURNED},
    LifecycleStage.ACTIVE: {LifecycleStage.ACTIVE, LifecycleStage.ADOPTION_RISK, LifecycleStage.RETENTION_RISK, LifecycleStage.EXPANSION, LifecycleStage.CHURNED},
    LifecycleStage.ADOPTION_RISK: {LifecycleStage.ADOPTION_RISK, LifecycleStage.RETENTION_RISK, LifecycleStage.RECOVERY, LifecycleStage.ACTIVE, LifecycleStage.CHURNED},
    LifecycleStage.RETENTION_RISK: {LifecycleStage.RETENTION_RISK, LifecycleStage.RECOVERY, LifecycleStage.ACTIVE, LifecycleStage.EXPANSION, LifecycleStage.CHURNED},
    LifecycleStage.RECOVERY: {LifecycleStage.RECOVERY, LifecycleStage.ACTIVE, LifecycleStage.EXPANSION, LifecycleStage.CHURNED},
    LifecycleStage.EXPANSION: {LifecycleStage.EXPANSION, LifecycleStage.ACTIVE, LifecycleStage.RETENTION_RISK, LifecycleStage.CHURNED},
    LifecycleStage.CHURNED: {LifecycleStage.CHURNED},
}


@dataclass(frozen=True)
class CustomerLifecycleSnapshot:
    workspace_id: str
    customer_id: str
    stage: LifecycleStage
    health_score: float
    positive_signals: float
    negative_signals: float
    interaction_count: int
    last_event_id: str
    last_event_name: str
    recommended_workflow: Optional[str] = None
    recommendation_reasons: tuple[str, ...] = ()
    version: int = 1

    def fingerprint(self) -> str:
        payload = {**self.__dict__, "stage": self.stage.value}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


@dataclass(frozen=True)
class LifecycleResult:
    accepted: bool
    customer_id: str
    stage: LifecycleStage
    health_score: float
    workflow_id: Optional[str]
    event_id: str
    reasons: tuple[str, ...] = ()
    snapshot: Optional[CustomerLifecycleSnapshot] = None
    execution: Any = None


class CustomerLifecycleRetention:
    VERSION = "v70.8"

    POSITIVE = {"activated": 12, "engaged": 8, "purchase": 15, "renewed": 20,
                "positive_feedback": 8, "recovered": 18, "expansion_interest": 12}
    NEGATIVE = {"inactive": 15, "unresponsive": 12, "complaint": 18,
                "payment_issue": 15, "retention_risk": 20, "churn_signal": 30}

    WORKFLOWS = {
        LifecycleStage.ONBOARDING: "customer.onboarding",
        LifecycleStage.ADOPTION_RISK: "customer.adoption-risk",
        LifecycleStage.RETENTION_RISK: "customer.retention-risk",
        LifecycleStage.RECOVERY: "customer.recovery",
        LifecycleStage.EXPANSION: "customer.expansion",
        LifecycleStage.CHURNED: "customer.churned",
    }

    def __init__(self, workspace_id: str, *, operating_layer: BusinessOperatingLayer):
        if not str(workspace_id).strip():
            raise ValueError("workspace_id is required")
        if operating_layer is None or operating_layer.workspace_id != str(workspace_id):
            raise ValueError(LifecycleBlock.TENANT_MISMATCH.value)
        self.workspace_id = str(workspace_id)
        self.operating_layer = operating_layer
        self._snapshots: dict[str, CustomerLifecycleSnapshot] = {}
        self._events: set[str] = set()

    def process(self, *, event: Mapping[str, Any], objective_id: str,
                approved: bool = False, now=None) -> LifecycleResult:
        event_id = str(event.get("event_id") or "").strip()
        if not event_id:
            raise ValueError(LifecycleBlock.MISSING_EVENT_ID.value)
        if event_id in self._events:
            raise ValueError(LifecycleBlock.DUPLICATE_EVENT.value)
        if str(event.get("workspace_id", self.workspace_id)) != self.workspace_id:
            raise ValueError(LifecycleBlock.TENANT_MISMATCH.value)
        customer_id = str(event.get("customer_id") or "").strip()
        if not customer_id:
            raise ValueError(LifecycleBlock.MISSING_CUSTOMER_ID.value)
        event_name = str(event.get("event_name") or "").strip()
        if event_name not in EVENT_STAGE:
            raise ValueError(LifecycleBlock.UNKNOWN_EVENT.value)

        previous = self._snapshots.get(customer_id)
        old_stage = previous.stage if previous else LifecycleStage.ONBOARDING
        target = EVENT_STAGE[event_name]
        if previous and old_stage == LifecycleStage.CHURNED and target != LifecycleStage.CHURNED:
            raise ValueError(LifecycleBlock.TERMINAL.value)
        if target not in ALLOWED[old_stage]:
            raise ValueError(f"invalid_transition:{old_stage.value}->{target.value}")

        signal = str(event.get("signal_type") or self._signal_from_event(event_name)).strip()
        if signal not in self.POSITIVE and signal not in self.NEGATIVE and signal != "neutral":
            raise ValueError(LifecycleBlock.INVALID_SIGNAL.value)
        pos = (previous.positive_signals if previous else 0.0) + float(self.POSITIVE.get(signal, 0))
        neg = (previous.negative_signals if previous else 0.0) + float(self.NEGATIVE.get(signal, 0))
        health = self._health(pos, neg)

        # Health is an evidence signal; the trusted lifecycle event remains the
        # authority for stage. We never auto-promote/demote from an AI score.
        workflow = self.WORKFLOWS.get(target)
        reasons = self._reasons(target, health, signal)
        snapshot = CustomerLifecycleSnapshot(
            workspace_id=self.workspace_id, customer_id=customer_id, stage=target,
            health_score=health, positive_signals=pos, negative_signals=neg,
            interaction_count=(previous.interaction_count + 1 if previous else 1),
            last_event_id=event_id, last_event_name=event_name,
            recommended_workflow=workflow, recommendation_reasons=reasons,
            version=(previous.version + 1 if previous else 1),
        )
        self._events.add(event_id)
        self._snapshots[customer_id] = snapshot

        execution = None
        if workflow:
            enriched = dict(event)
            enriched.update({"customer_lifecycle_stage": target.value, "customer_health_score": health,
                             "customer_lifecycle_reasons": list(reasons)})
            execution = None  # explicit: callers must use governed async route below
        return LifecycleResult(True, customer_id, target, health, workflow, event_id, reasons, snapshot, execution)

    async def handle_event(self, *, event: Mapping[str, Any], objective_id: str,
                           approved: bool = False, now=None) -> LifecycleResult:
        result = self.process(event=event, objective_id=objective_id, approved=approved, now=now)
        if not result.workflow_id:
            return result
        enriched = dict(event)
        enriched.update({"customer_lifecycle_stage": result.stage.value,
                         "customer_health_score": result.health_score,
                         "customer_lifecycle_reasons": list(result.reasons)})
        run = await self.operating_layer.handle_event(objective_id, result.workflow_id,
                                                      event=enriched, approved=approved, now=now)
        return LifecycleResult(result.accepted, result.customer_id, result.stage,
                               result.health_score, result.workflow_id, result.event_id,
                               result.reasons, result.snapshot, run)

    def snapshot(self, customer_id: str) -> CustomerLifecycleSnapshot | None:
        return self._snapshots.get(str(customer_id).strip())

    def all_snapshots(self) -> tuple[CustomerLifecycleSnapshot, ...]:
        return tuple(self._snapshots.values())

    @staticmethod
    def _health(positive: float, negative: float) -> float:
        # Explainable bounded score: 50 baseline, +/- signal pressure, no ML claim.
        return round(max(0.0, min(100.0, 50.0 + positive - negative)), 2)

    @staticmethod
    def _signal_from_event(event_name: str) -> str:
        return {
            "customer.onboarding_started": "neutral", "customer.activated": "activated",
            "customer.adoption_risk": "inactive", "customer.retention_risk": "retention_risk",
            "customer.recovery_started": "recovered", "customer.expansion_opportunity": "expansion_interest",
            "customer.churned": "churn_signal", "customer.recovered": "recovered",
        }[event_name]

    @staticmethod
    def _reasons(stage: LifecycleStage, health: float, signal: str) -> tuple[str, ...]:
        reasons = [f"trusted_signal:{signal}", f"health_score:{health:g}"]
        if stage == LifecycleStage.ADOPTION_RISK: reasons.append("adoption_attention_required")
        if stage == LifecycleStage.RETENTION_RISK: reasons.append("retention_attention_required")
        if stage == LifecycleStage.RECOVERY: reasons.append("recovery_workflow_required")
        if stage == LifecycleStage.EXPANSION: reasons.append("expansion_opportunity_detected")
        if stage == LifecycleStage.CHURNED: reasons.append("customer_churn_confirmed")
        return tuple(reasons)


__all__ = ["CustomerLifecycleRetention", "CustomerLifecycleSnapshot", "LifecycleResult", "LifecycleStage", "LifecycleBlock"]
