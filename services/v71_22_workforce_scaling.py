"""V71.22 — Governed Workforce Scaling & Coordination.

This module decides whether a worker pool should *seek* a different capacity level.
It does not provision workers, mutate infrastructure, authorize actions, or execute tasks.
A scaling decision is advisory and must be handed to a separately governed provisioning
boundary if a deployment system is ever allowed to materialize worker instances.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional
import hashlib, json

from services.v71_21_worker_registry import WorkerRegistry, WorkerLifecycle


class ScalingDisposition(str, Enum):
    HOLD = "hold"
    NO_CHANGE = "no_change"
    SCALE_UP = "scale_up"
    SCALE_DOWN = "scale_down"


class ScalingBlock(str, Enum):
    TENANT_MISMATCH = "tenant_mismatch"
    POOL_NOT_FOUND = "worker_pool_not_found"
    INVALID_POLICY = "invalid_scaling_policy"
    INVALID_OBSERVATION = "invalid_scaling_observation"
    POLICY_TAMPERED = "scaling_policy_tampered"
    DECISION_TAMPERED = "scaling_decision_tampered"
    SAFETY_HOLD = "scaling_safety_hold"


@dataclass(frozen=True)
class ScalingPolicy:
    policy_id: str
    workspace_id: str
    min_instances: int
    max_instances: int
    target_utilization: float = 0.70
    scale_up_threshold: float = 0.80
    scale_down_threshold: float = 0.30
    max_step: int = 2
    cooldown_observations: int = 0
    allow_scale_down: bool = True
    safety_max_failure_rate: float = 0.25
    policy_fingerprint: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id, "workspace_id": self.workspace_id,
            "min_instances": self.min_instances, "max_instances": self.max_instances,
            "target_utilization": self.target_utilization,
            "scale_up_threshold": self.scale_up_threshold,
            "scale_down_threshold": self.scale_down_threshold,
            "max_step": self.max_step, "cooldown_observations": self.cooldown_observations,
            "allow_scale_down": self.allow_scale_down,
            "safety_max_failure_rate": self.safety_max_failure_rate,
        }

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class ScalingObservation:
    observation_id: str
    workspace_id: str
    pool_id: str
    queue_depth: int
    active_tasks: int
    capacity_slots: int
    failure_rate: float = 0.0
    recent_scale_changes: int = 0
    emergency_stop: bool = False
    capability_drift: bool = False
    observation_fingerprint: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id, "workspace_id": self.workspace_id,
            "pool_id": self.pool_id, "queue_depth": self.queue_depth,
            "active_tasks": self.active_tasks, "capacity_slots": self.capacity_slots,
            "failure_rate": self.failure_rate, "recent_scale_changes": self.recent_scale_changes,
            "emergency_stop": self.emergency_stop, "capability_drift": self.capability_drift,
        }

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class ScalingDecision:
    decision_id: str
    workspace_id: str
    pool_id: str
    disposition: ScalingDisposition
    current_instances: int
    requested_instances: int
    reason_codes: tuple[str, ...] = ()
    policy_fingerprint: str = ""
    observation_fingerprint: str = ""
    advisory_only: bool = True
    provisioning_authority_granted: bool = False
    execution_requested: bool = False
    decision_fingerprint: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id, "workspace_id": self.workspace_id,
            "pool_id": self.pool_id, "disposition": self.disposition.value,
            "current_instances": self.current_instances, "requested_instances": self.requested_instances,
            "reason_codes": list(self.reason_codes), "policy_fingerprint": self.policy_fingerprint,
            "observation_fingerprint": self.observation_fingerprint,
            "advisory_only": self.advisory_only,
            "provisioning_authority_granted": self.provisioning_authority_granted,
            "execution_requested": self.execution_requested,
        }

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class WorkforceScalingCoordinator:
    VERSION = "v71.22"

    def __init__(self, workspace_id: str, worker_registry: WorkerRegistry):
        ws = str(workspace_id or "").strip()
        if not ws or worker_registry.workspace_id != ws:
            raise ValueError(ScalingBlock.TENANT_MISMATCH.value)
        self.workspace_id = ws
        self.worker_registry = worker_registry
        self._policies: dict[str, ScalingPolicy] = {}
        self._observations: dict[str, ScalingObservation] = {}
        self._decisions: dict[str, ScalingDecision] = {}

    def register_policy(self, policy: ScalingPolicy) -> ScalingPolicy:
        if policy.workspace_id != self.workspace_id:
            raise ValueError(ScalingBlock.TENANT_MISMATCH.value)
        if policy.policy_fingerprint and policy.policy_fingerprint != policy.fingerprint():
            raise ValueError(ScalingBlock.POLICY_TAMPERED.value)
        if (policy.min_instances < 0 or policy.max_instances < max(1, policy.min_instances)
                or not 0 <= policy.scale_down_threshold < policy.scale_up_threshold <= 1
                or not 0 <= policy.target_utilization <= 1
                or policy.max_step < 1 or policy.cooldown_observations < 0
                or not 0 <= policy.safety_max_failure_rate <= 1):
            raise ValueError(ScalingBlock.INVALID_POLICY.value)
        sealed = ScalingPolicy(**{**policy.__dict__, "policy_fingerprint": policy.fingerprint()})
        self._policies[sealed.policy_id] = sealed
        return sealed

    def observe(self, observation: ScalingObservation) -> ScalingObservation:
        if observation.workspace_id != self.workspace_id:
            raise ValueError(ScalingBlock.TENANT_MISMATCH.value)
        if observation.observation_fingerprint and observation.observation_fingerprint != observation.fingerprint():
            raise ValueError(ScalingBlock.INVALID_OBSERVATION.value)
        if self.worker_registry.get_pool(observation.pool_id) is None:
            raise ValueError(ScalingBlock.POOL_NOT_FOUND.value)
        if observation.queue_depth < 0 or observation.active_tasks < 0 or observation.capacity_slots < 0 or not 0 <= observation.failure_rate <= 1:
            raise ValueError(ScalingBlock.INVALID_OBSERVATION.value)
        sealed = ScalingObservation(**{**observation.__dict__, "observation_fingerprint": observation.fingerprint()})
        self._observations[sealed.observation_id] = sealed
        return sealed

    def decide(self, *, decision_id: str, policy_id: str, observation_id: str) -> ScalingDecision:
        if decision_id in self._decisions:
            raise ValueError(ScalingBlock.INVALID_OBSERVATION.value)
        policy = self._policies.get(policy_id)
        observation = self._observations.get(observation_id)
        if policy is None or observation is None:
            raise ValueError(ScalingBlock.INVALID_POLICY.value if policy is None else ScalingBlock.INVALID_OBSERVATION.value)
        if policy.workspace_id != self.workspace_id or observation.workspace_id != self.workspace_id:
            raise ValueError(ScalingBlock.TENANT_MISMATCH.value)
        if policy.policy_fingerprint != policy.fingerprint():
            raise ValueError(ScalingBlock.POLICY_TAMPERED.value)
        if observation.observation_fingerprint != observation.fingerprint():
            raise ValueError(ScalingBlock.INVALID_OBSERVATION.value)
        pool = self.worker_registry.get_pool(observation.pool_id)
        if pool is None:
            raise ValueError(ScalingBlock.POOL_NOT_FOUND.value)
        self.worker_registry.assert_pool_integrity(pool)
        if policy.min_instances < pool.min_instances or policy.max_instances > pool.max_instances:
            raise ValueError(ScalingBlock.INVALID_POLICY.value)
        current = self.worker_registry.pool_counts(pool.pool_id).get(WorkerLifecycle.ACTIVE.value, 0)
        reasons: list[str] = []
        disposition = ScalingDisposition.NO_CHANGE
        requested = current

        if observation.emergency_stop or observation.capability_drift:
            disposition = ScalingDisposition.HOLD
            reasons.append("safety_signal")
        elif observation.failure_rate > policy.safety_max_failure_rate:
            disposition = ScalingDisposition.HOLD
            reasons.append("failure_rate_above_safety_limit")
        elif observation.recent_scale_changes < 0:
            disposition = ScalingDisposition.HOLD
            reasons.append("invalid_recent_scale_history")
        elif observation.recent_scale_changes < policy.cooldown_observations:
            disposition = ScalingDisposition.HOLD
            reasons.append("cooldown_active")
        else:
            utilization = observation.active_tasks / max(1, observation.capacity_slots)
            pressure = observation.queue_depth > 0 or utilization >= policy.scale_up_threshold
            relief = observation.queue_depth == 0 and utilization <= policy.scale_down_threshold
            if pressure and current < policy.max_instances:
                disposition = ScalingDisposition.SCALE_UP
                requested = min(policy.max_instances, current + min(policy.max_step, max(1, observation.queue_depth)))
                reasons.append("capacity_pressure")
            elif relief and policy.allow_scale_down and current > policy.min_instances:
                disposition = ScalingDisposition.SCALE_DOWN
                requested = max(policy.min_instances, current - min(policy.max_step, 1))
                reasons.append("capacity_surplus")
            else:
                reasons.append("capacity_within_policy")

        decision = ScalingDecision(
            decision_id, self.workspace_id, pool.pool_id, disposition, current, requested,
            tuple(reasons), policy.policy_fingerprint, observation.observation_fingerprint,
            True, False, False, "")
        decision = ScalingDecision(**{**decision.__dict__, "decision_fingerprint": decision.fingerprint()})
        self._decisions[decision_id] = decision
        return decision

    def get_decision(self, decision_id: str) -> Optional[ScalingDecision]:
        return self._decisions.get(decision_id)

    def assert_integrity(self, decision: ScalingDecision) -> None:
        if decision.workspace_id != self.workspace_id:
            raise ValueError(ScalingBlock.TENANT_MISMATCH.value)
        if decision.decision_fingerprint != decision.fingerprint():
            raise ValueError(ScalingBlock.DECISION_TAMPERED.value)

    def context(self, decision_id: str) -> Mapping[str, Any]:
        decision = self.get_decision(decision_id)
        if decision is None:
            raise ValueError(ScalingBlock.INVALID_OBSERVATION.value)
        self.assert_integrity(decision)
        return {
            "decision_id": decision.decision_id,
            "pool_id": decision.pool_id,
            "disposition": decision.disposition.value,
            "current_instances": decision.current_instances,
            "requested_instances": decision.requested_instances,
            "advisory_only": True,
            "provisioning_authority_granted": False,
            "execution_requested": False,
        }


__all__ = ["ScalingDisposition", "ScalingBlock", "ScalingPolicy", "ScalingObservation", "ScalingDecision", "WorkforceScalingCoordinator"]
