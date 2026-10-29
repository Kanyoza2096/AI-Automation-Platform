"""Phase 7.37 — Workforce Coordination Policy Enforcement & Consumption Binding.

Proves that adaptive-planning assessments consume the exact currently ACTIVE,
governance-approved coordination policy. This is an enforcement/evidence layer;
it does not create policy authority, authorize execution, mutate plans, call
providers, or invoke the Action Kernel.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import hashlib, json, sqlite3

from services.v71_46_workforce_adaptive_planning import (
    WorkforceAdaptivePlanner, WorkforceCoordinationPolicy, AdaptivePlanningAssessment,
)
from services.v71_47_workforce_coordination_policy_governance import (
    WorkforceCoordinationPolicyGovernance, CoordinationPolicyGovernanceBlock,
)

class CoordinationPolicyEnforcementBlock(str):
    TENANT_MISMATCH = "tenant_mismatch"
    POLICY_MISSING = "policy_missing"
    POLICY_TAMPERED = "policy_tampered"
    POLICY_MISMATCH = "policy_mismatch"
    STALE_POLICY = "stale_policy"
    NOT_ACTIVE = "policy_not_active"
    DUPLICATE = "duplicate"
    BINDING_TAMPERED = "binding_tampered"
    ASSESSMENT_TAMPERED = "assessment_tampered"
    AUTHORITY_VIOLATION = "authority_violation"

@dataclass(frozen=True)
class CoordinationPolicyConsumptionBinding:
    binding_id: str
    workspace_id: str
    assessment_id: str
    policy_id: str
    policy_version: str
    policy_fingerprint: str
    governance_change_id: str
    governance_state: str
    binding_fingerprint: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if k != "binding_fingerprint"}

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()

class WorkforceCoordinationPolicyEnforcement:
    VERSION = "phase7.37"

    def __init__(self, workspace_id: str, governance: WorkforceCoordinationPolicyGovernance,
                 adaptive: WorkforceAdaptivePlanner, db_path: str = ":memory:"):
        ws = str(workspace_id or "").strip()
        if not ws or governance.workspace_id != ws or adaptive.workspace_id != ws:
            raise ValueError(CoordinationPolicyEnforcementBlock.TENANT_MISMATCH)
        self.workspace_id, self.governance, self.adaptive = ws, governance, adaptive
        self._db = sqlite3.connect(db_path)
        self._db.execute("""CREATE TABLE IF NOT EXISTS workforce_coordination_policy_consumption_bindings(
            binding_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, assessment_id TEXT NOT NULL,
            policy_id TEXT NOT NULL, policy_version TEXT NOT NULL, policy_fingerprint TEXT NOT NULL,
            governance_change_id TEXT NOT NULL, governance_state TEXT NOT NULL,
            binding_fingerprint TEXT NOT NULL, UNIQUE(workspace_id, assessment_id))""")
        self._db.commit()

    def _get(self, binding_id: str):
        row = self._db.execute("""SELECT binding_id,workspace_id,assessment_id,policy_id,
            policy_version,policy_fingerprint,governance_change_id,governance_state,binding_fingerprint
            FROM workforce_coordination_policy_consumption_bindings
            WHERE binding_id=? AND workspace_id=?""", (str(binding_id), self.workspace_id)).fetchone()
        return CoordinationPolicyConsumptionBinding(*row) if row else None

    def _validate_policy(self, policy_id: str, policy: WorkforceCoordinationPolicy):
        policy.validate()
        active = self.governance.active(policy_id)
        if active is None:
            raise ValueError(CoordinationPolicyEnforcementBlock.POLICY_MISSING)
        try:
            self.governance.assert_integrity(active)
        except ValueError as exc:
            raise ValueError(CoordinationPolicyEnforcementBlock.POLICY_TAMPERED) from exc
        if active.state != "ACTIVE":
            raise ValueError(CoordinationPolicyEnforcementBlock.NOT_ACTIVE)
        expected_fp = policy.fingerprint()
        if str(active.version) != str(policy.version):
            raise ValueError(CoordinationPolicyEnforcementBlock.STALE_POLICY)
        if active.policy_fingerprint != expected_fp:
            raise ValueError(CoordinationPolicyEnforcementBlock.POLICY_MISMATCH)
        return active

    def bind_assessment(self, *, binding_id: str, assessment: AdaptivePlanningAssessment,
                        policy_id: str, policy: WorkforceCoordinationPolicy) -> CoordinationPolicyConsumptionBinding:
        if assessment.workspace_id != self.workspace_id:
            raise ValueError(CoordinationPolicyEnforcementBlock.TENANT_MISMATCH)
        if self._get(binding_id) is not None:
            raise ValueError(CoordinationPolicyEnforcementBlock.DUPLICATE)
        if self._db.execute("SELECT 1 FROM workforce_coordination_policy_consumption_bindings WHERE workspace_id=? AND assessment_id=?", (self.workspace_id, assessment.assessment_id)).fetchone():
            raise ValueError(CoordinationPolicyEnforcementBlock.DUPLICATE)
        try:
            self.adaptive.assert_integrity(assessment)
        except ValueError as exc:
            raise ValueError(CoordinationPolicyEnforcementBlock.ASSESSMENT_TAMPERED) from exc
        active = self._validate_policy(policy_id, policy)
        if assessment.policy_version != str(policy.version) or assessment.policy_fingerprint != policy.fingerprint():
            raise ValueError(CoordinationPolicyEnforcementBlock.ASSESSMENT_TAMPERED)
        b = CoordinationPolicyConsumptionBinding(
            str(binding_id), self.workspace_id, assessment.assessment_id, str(policy_id),
            str(active.version), active.policy_fingerprint, active.change_id, active.state,
        )
        b = CoordinationPolicyConsumptionBinding(**{**b.__dict__, "binding_fingerprint": b.fingerprint()})
        self._db.execute("INSERT INTO workforce_coordination_policy_consumption_bindings VALUES (?,?,?,?,?,?,?,?,?)", tuple(b.__dict__.values()))
        self._db.commit()
        return b

    def assert_consumable(self, *, policy_id: str, policy: WorkforceCoordinationPolicy,
                          assessment: AdaptivePlanningAssessment | None = None) -> Any:
        active = self._validate_policy(policy_id, policy)
        if assessment is None:
            return active
        if assessment.workspace_id != self.workspace_id:
            raise ValueError(CoordinationPolicyEnforcementBlock.TENANT_MISMATCH)
        binding = self.binding_for_assessment(assessment.assessment_id)
        if binding is None:
            raise ValueError(CoordinationPolicyEnforcementBlock.POLICY_MISSING)
        self.assert_binding_integrity(binding)
        if (binding.policy_id != str(policy_id) or binding.policy_version != str(active.version) or
            binding.policy_fingerprint != active.policy_fingerprint or binding.governance_change_id != active.change_id or
            binding.governance_state != "ACTIVE"):
            raise ValueError(CoordinationPolicyEnforcementBlock.STALE_POLICY)
        self.governance.assert_active_binding(policy_id=policy_id, version=active.version, policy_fingerprint=active.policy_fingerprint)
        if assessment.policy_version != binding.policy_version or assessment.policy_fingerprint != binding.policy_fingerprint:
            raise ValueError(CoordinationPolicyEnforcementBlock.ASSESSMENT_TAMPERED)
        return binding

    def assess_governed(self, *, assessment_id: str, binding_id: str, policy_id: str,
                        plan, policy: WorkforceCoordinationPolicy, feedback_ids: tuple[str, ...]) -> AdaptivePlanningAssessment:
        # The active governance binding is checked BEFORE adaptive computation.
        self._validate_policy(policy_id, policy)
        assessment = self.adaptive.assess(assessment_id=assessment_id, plan=plan, policy=policy, feedback_ids=feedback_ids)
        self.bind_assessment(binding_id=binding_id, assessment=assessment, policy_id=policy_id, policy=policy)
        return assessment

    def binding_for_assessment(self, assessment_id: str):
        row = self._db.execute("""SELECT binding_id,workspace_id,assessment_id,policy_id,policy_version,
            policy_fingerprint,governance_change_id,governance_state,binding_fingerprint
            FROM workforce_coordination_policy_consumption_bindings
            WHERE assessment_id=? AND workspace_id=?""", (str(assessment_id), self.workspace_id)).fetchone()
        return CoordinationPolicyConsumptionBinding(*row) if row else None

    def assert_binding_integrity(self, binding: CoordinationPolicyConsumptionBinding) -> None:
        if binding.workspace_id != self.workspace_id or binding.binding_fingerprint != binding.fingerprint():
            raise ValueError(CoordinationPolicyEnforcementBlock.BINDING_TAMPERED)
        stored = self._get(binding.binding_id)
        if stored is None or stored.binding_fingerprint != binding.binding_fingerprint:
            raise ValueError(CoordinationPolicyEnforcementBlock.BINDING_TAMPERED)
        active = self.governance.active(binding.policy_id)
        if active is None:
            raise ValueError(CoordinationPolicyEnforcementBlock.POLICY_MISSING)
        try:
            self.governance.assert_integrity(active)
        except ValueError as exc:
            raise ValueError(CoordinationPolicyEnforcementBlock.POLICY_TAMPERED) from exc
        if (active.state != "ACTIVE" or str(active.version) != binding.policy_version or
            active.policy_fingerprint != binding.policy_fingerprint or active.change_id != binding.governance_change_id):
            raise ValueError(CoordinationPolicyEnforcementBlock.STALE_POLICY)

    def context(self, assessment_id: str) -> dict[str, Any]:
        binding = self.binding_for_assessment(assessment_id)
        if binding is None:
            raise ValueError(CoordinationPolicyEnforcementBlock.POLICY_MISSING)
        self.assert_binding_integrity(binding)
        return {
            "assessment_id": binding.assessment_id,
            "policy_id": binding.policy_id,
            "policy_version": binding.policy_version,
            "policy_fingerprint": binding.policy_fingerprint,
            "governance_change_id": binding.governance_change_id,
            "governance_state": binding.governance_state,
            "binding_fingerprint": binding.binding_fingerprint,
            "advisory_only": True,
            "authority": "none",
            "can_authorize": False,
            "can_execute": False,
            "can_call_provider": False,
            "can_invoke_action_kernel": False,
        }

    def close(self):
        self._db.close()

__all__ = ["CoordinationPolicyEnforcementBlock", "CoordinationPolicyConsumptionBinding", "WorkforceCoordinationPolicyEnforcement"]
