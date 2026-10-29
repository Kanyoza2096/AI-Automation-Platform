"""V71.23 — Governed Workforce Demand Planning.

Transforms bounded operational demand signals into an auditable workforce capacity
plan. Planning is advisory only: it never creates workers, changes registry state,
authorizes actions, invokes the Action Kernel, or calls external providers.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any
import hashlib, json

from services.v71_21_worker_registry import WorkerRegistry
from services.v71_22_workforce_scaling import ScalingPolicy, ScalingObservation, WorkforceScalingCoordinator, ScalingDisposition


class DemandDisposition(str, Enum):
    HOLD = "hold"
    STABLE = "stable"
    EXPAND = "expand"
    CONTRACT = "contract"


class DemandBlock(str, Enum):
    TENANT_MISMATCH = "tenant_mismatch"
    POLICY_MISMATCH = "policy_mismatch"
    OBSERVATION_MISMATCH = "observation_mismatch"
    INVALID_INPUT = "invalid_input"
    SAFETY_HOLD = "safety_hold"
    PLAN_TAMPERED = "plan_tampered"


@dataclass(frozen=True)
class DemandForecast:
    forecast_id: str
    workspace_id: str
    pool_id: str
    expected_queue_depth: int
    expected_active_tasks: int
    horizon_observations: int
    confidence: float
    source: str = "bounded_operational_forecast"
    forecast_fingerprint: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        return {"forecast_id": self.forecast_id, "workspace_id": self.workspace_id,
                "pool_id": self.pool_id, "expected_queue_depth": self.expected_queue_depth,
                "expected_active_tasks": self.expected_active_tasks, "horizon_observations": self.horizon_observations,
                "confidence": self.confidence, "source": self.source}

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class WorkforceDemandPlan:
    plan_id: str
    workspace_id: str
    pool_id: str
    disposition: DemandDisposition
    current_instances: int
    planned_instances: int
    min_instances: int
    max_instances: int
    reason_codes: tuple[str, ...]
    policy_fingerprint: str
    observation_fingerprint: str
    forecast_fingerprint: str = ""
    advisory_only: bool = True
    provisioning_authority_granted: bool = False
    execution_requested: bool = False
    plan_fingerprint: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        return {"plan_id": self.plan_id, "workspace_id": self.workspace_id, "pool_id": self.pool_id,
                "disposition": self.disposition.value, "current_instances": self.current_instances,
                "planned_instances": self.planned_instances, "min_instances": self.min_instances,
                "max_instances": self.max_instances, "reason_codes": list(self.reason_codes),
                "policy_fingerprint": self.policy_fingerprint, "observation_fingerprint": self.observation_fingerprint,
                "forecast_fingerprint": self.forecast_fingerprint, "advisory_only": self.advisory_only,
                "provisioning_authority_granted": self.provisioning_authority_granted,
                "execution_requested": self.execution_requested}

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class WorkforceDemandPlanner:
    VERSION = "v71.23"

    def __init__(self, workspace_id: str, worker_registry: WorkerRegistry, scaling: WorkforceScalingCoordinator):
        ws = str(workspace_id or "").strip()
        if not ws or worker_registry.workspace_id != ws or scaling.workspace_id != ws:
            raise ValueError(DemandBlock.TENANT_MISMATCH.value)
        self.workspace_id = ws
        self.worker_registry = worker_registry
        self.scaling = scaling
        self._forecasts: dict[str, DemandForecast] = {}
        self._plans: dict[str, WorkforceDemandPlan] = {}

    def record_forecast(self, forecast: DemandForecast) -> DemandForecast:
        if forecast.workspace_id != self.workspace_id:
            raise ValueError(DemandBlock.TENANT_MISMATCH.value)
        if self.worker_registry.get_pool(forecast.pool_id) is None:
            raise ValueError(DemandBlock.INVALID_INPUT.value)
        if forecast.expected_queue_depth < 0 or forecast.expected_active_tasks < 0 or forecast.horizon_observations < 1 or not 0 <= forecast.confidence <= 1:
            raise ValueError(DemandBlock.INVALID_INPUT.value)
        if forecast.forecast_fingerprint and forecast.forecast_fingerprint != forecast.fingerprint():
            raise ValueError(DemandBlock.INVALID_INPUT.value)
        sealed = DemandForecast(**{**forecast.__dict__, "forecast_fingerprint": forecast.fingerprint()})
        self._forecasts[sealed.forecast_id] = sealed
        return sealed

    def plan(self, *, plan_id: str, policy: ScalingPolicy, observation: ScalingObservation, forecast_id: str | None = None) -> WorkforceDemandPlan:
        if plan_id in self._plans:
            raise ValueError(DemandBlock.INVALID_INPUT.value)
        if policy.workspace_id != self.workspace_id or observation.workspace_id != self.workspace_id:
            raise ValueError(DemandBlock.TENANT_MISMATCH.value)
        if observation.pool_id != self.worker_registry.get_pool(observation.pool_id).pool_id:
            raise ValueError(DemandBlock.OBSERVATION_MISMATCH.value)
        forecast = self._forecasts.get(forecast_id) if forecast_id else None
        if forecast_id and forecast is None:
            raise ValueError(DemandBlock.INVALID_INPUT.value)
        if forecast and forecast.pool_id != observation.pool_id:
            raise ValueError(DemandBlock.OBSERVATION_MISMATCH.value)
        if forecast and forecast.workspace_id != self.workspace_id:
            raise ValueError(DemandBlock.TENANT_MISMATCH.value)
        if policy.policy_fingerprint != policy.fingerprint() or observation.observation_fingerprint != observation.fingerprint():
            raise ValueError(DemandBlock.PLAN_TAMPERED.value)
        if forecast and forecast.forecast_fingerprint != forecast.fingerprint():
            raise ValueError(DemandBlock.PLAN_TAMPERED.value)

        pool = self.worker_registry.get_pool(observation.pool_id)
        self.worker_registry.assert_pool_integrity(pool)
        if policy.min_instances < pool.min_instances or policy.max_instances > pool.max_instances:
            raise ValueError(DemandBlock.POLICY_MISMATCH.value)
        current = self.worker_registry.pool_counts(pool.pool_id).get("active", 0)

        if observation.emergency_stop or observation.capability_drift or observation.failure_rate > policy.safety_max_failure_rate:
            disposition, planned, reasons = DemandDisposition.HOLD, current, ["safety_hold"]
        else:
            queue = forecast.expected_queue_depth if forecast else observation.queue_depth
            active = forecast.expected_active_tasks if forecast else observation.active_tasks
            confidence = forecast.confidence if forecast else 1.0
            util = active / max(1, observation.capacity_slots)
            pressure = queue > 0 or util >= policy.scale_up_threshold
            relief = queue == 0 and util <= policy.scale_down_threshold
            if forecast and confidence < 0.60:
                disposition, planned, reasons = DemandDisposition.HOLD, current, ["forecast_confidence_low"]
            elif pressure and current < policy.max_instances:
                step = min(policy.max_step, max(1, queue))
                planned = min(policy.max_instances, current + step)
                disposition, reasons = DemandDisposition.EXPAND, ["forecast_capacity_pressure" if forecast else "observed_capacity_pressure"]
            elif relief and policy.allow_scale_down and current > policy.min_instances:
                planned = max(policy.min_instances, current - min(policy.max_step, 1))
                disposition, reasons = DemandDisposition.CONTRACT, ["capacity_relief"]
            else:
                planned, reasons = current, ["capacity_stable"]
                disposition = DemandDisposition.STABLE

        plan = WorkforceDemandPlan(plan_id, self.workspace_id, pool.pool_id, disposition, current, planned,
                                   policy.min_instances, policy.max_instances, tuple(reasons),
                                   policy.policy_fingerprint, observation.observation_fingerprint,
                                   forecast.forecast_fingerprint if forecast else "", True, False, False, "")
        plan = WorkforceDemandPlan(**{**plan.__dict__, "plan_fingerprint": plan.fingerprint()})
        self._plans[plan.plan_id] = plan
        return plan

    def assert_integrity(self, plan: WorkforceDemandPlan) -> None:
        if plan.workspace_id != self.workspace_id:
            raise ValueError(DemandBlock.TENANT_MISMATCH.value)
        if not plan.advisory_only or plan.provisioning_authority_granted or plan.execution_requested:
            raise ValueError(DemandBlock.PLAN_TAMPERED.value)
        if plan.plan_fingerprint != plan.fingerprint():
            raise ValueError(DemandBlock.PLAN_TAMPERED.value)
