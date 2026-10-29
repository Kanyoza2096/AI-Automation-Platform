"""Phase 7.22 — Worker Selection Policy Enforcement & Activation Binding.

Binds the governed ACTIVE policy change to the policy profile actually consumed by
worker selection. Approval alone is insufficient. The binding is durable,
workspace-scoped, fingerprinted, and fail-closed. This layer never authorizes or
executes worker actions.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Mapping, Optional
import hashlib, json, sqlite3

from services.v71_31_worker_selection_policy import WorkerSelectionPolicyRegistry, SelectionPolicyBlock
from services.v71_32_worker_selection_policy_governance import WorkerSelectionPolicyGovernance, PolicyGovernanceBlock


class PolicyActivationBlock(str):
    TENANT_MISMATCH = "tenant_mismatch"
    POLICY_MISSING = "policy_missing"
    GOVERNANCE_MISSING = "governance_missing"
    NOT_ACTIVE = "policy_not_active"
    FINGERPRINT_MISMATCH = "fingerprint_mismatch"
    ALREADY_BOUND = "already_bound"
    BINDING_MISSING = "binding_missing"
    BINDING_TAMPERED = "binding_tampered"
    INVALID_BINDING = "invalid_binding"
    CAPABILITY_BLOCKED = "capability_blocked"
    AUTHORITY_VIOLATION = "authority_violation"


@dataclass(frozen=True)
class PolicyActivationBinding:
    binding_id: str
    workspace_id: str
    policy_id: str
    version: int
    change_id: str
    policy_fingerprint: str
    activated_by: str
    state: str = "BOUND"
    binding_fingerprint: str = ""

    def payload(self) -> dict[str, Any]:
        return {
            "binding_id": self.binding_id, "workspace_id": self.workspace_id,
            "policy_id": self.policy_id, "version": self.version,
            "change_id": self.change_id, "policy_fingerprint": self.policy_fingerprint,
            "activated_by": self.activated_by, "state": self.state,
        }

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.payload(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class WorkerSelectionPolicyActivation:
    VERSION = "phase7.22"

    def __init__(self, workspace_id: str, policy_registry: WorkerSelectionPolicyRegistry,
                 governance: WorkerSelectionPolicyGovernance, db_path: str = ":memory:"):
        ws = str(workspace_id or "").strip()
        if not ws or policy_registry.workspace_id != ws or governance.workspace_id != ws:
            raise ValueError(PolicyActivationBlock.TENANT_MISMATCH)
        self.workspace_id = ws
        self.policy_registry = policy_registry
        self.governance = governance
        self._db = sqlite3.connect(db_path)
        self._db.execute("""CREATE TABLE IF NOT EXISTS worker_selection_policy_activation (
            binding_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, policy_id TEXT NOT NULL,
            version INTEGER NOT NULL, change_id TEXT NOT NULL, policy_fingerprint TEXT NOT NULL,
            activated_by TEXT NOT NULL, state TEXT NOT NULL, binding_fingerprint TEXT NOT NULL,
            UNIQUE(workspace_id, policy_id)
        )""")
        self._db.commit()

    def bind_active(self, *, binding_id: str, policy_id: str, version: int,
                    change_id: str, activated_by: str) -> PolicyActivationBinding:
        if not binding_id or not policy_id or not change_id or not activated_by or int(version) < 1:
            raise ValueError(PolicyActivationBlock.INVALID_BINDING)
        policy = self.policy_registry.get(policy_id, int(version))
        if policy is None:
            raise ValueError(PolicyActivationBlock.POLICY_MISSING)
        self.policy_registry.assert_integrity(policy)
        change = self.governance._row(change_id)
        if change is None or change.workspace_id != self.workspace_id:
            raise ValueError(PolicyActivationBlock.GOVERNANCE_MISSING)
        if change.state != "ACTIVE":
            raise ValueError(PolicyActivationBlock.NOT_ACTIVE)
        if change.policy_id != policy_id or change.version != int(version):
            raise ValueError(PolicyActivationBlock.FINGERPRINT_MISMATCH)
        if change.policy_fingerprint != policy.policy_fingerprint:
            raise ValueError(PolicyActivationBlock.FINGERPRINT_MISMATCH)
        existing = self.active(policy_id)
        if existing is not None:
            if existing.change_id == change_id and existing.version == int(version):
                return existing
            raise ValueError(PolicyActivationBlock.ALREADY_BOUND)
        binding = PolicyActivationBinding(binding_id, self.workspace_id, policy_id, int(version), change_id,
                                          policy.policy_fingerprint, activated_by)
        binding = PolicyActivationBinding(**{**binding.__dict__, "binding_fingerprint": binding.fingerprint()})
        self._db.execute("INSERT INTO worker_selection_policy_activation VALUES (?,?,?,?,?,?,?,?,?)",
                         tuple(binding.__dict__.values()))
        self._db.commit()
        return binding

    def active(self, policy_id: str) -> Optional[PolicyActivationBinding]:
        row = self._db.execute("SELECT binding_id,workspace_id,policy_id,version,change_id,policy_fingerprint,activated_by,state,binding_fingerprint FROM worker_selection_policy_activation WHERE workspace_id=? AND policy_id=? AND state='BOUND'", (self.workspace_id, str(policy_id))).fetchone()
        return PolicyActivationBinding(*row) if row else None

    def assert_integrity(self, binding: PolicyActivationBinding) -> None:
        if binding.workspace_id != self.workspace_id or binding.state != "BOUND":
            raise ValueError(PolicyActivationBlock.BINDING_TAMPERED)
        if binding.binding_fingerprint != binding.fingerprint():
            raise ValueError(PolicyActivationBlock.BINDING_TAMPERED)
        policy = self.policy_registry.get(binding.policy_id, binding.version)
        if policy is None:
            raise ValueError(PolicyActivationBlock.POLICY_MISSING)
        self.policy_registry.assert_integrity(policy)
        change = self.governance._row(binding.change_id)
        if change is None or change.state != "ACTIVE":
            raise ValueError(PolicyActivationBlock.NOT_ACTIVE)
        if change.policy_fingerprint != binding.policy_fingerprint or policy.policy_fingerprint != binding.policy_fingerprint:
            raise ValueError(PolicyActivationBlock.FINGERPRINT_MISMATCH)

    def enforce(self, *, policy_id: str, capability: str) -> Mapping[str, Any]:
        binding = self.active(policy_id)
        if binding is None:
            raise ValueError(PolicyActivationBlock.BINDING_MISSING)
        self.assert_integrity(binding)
        policy = self.policy_registry.get(binding.policy_id, binding.version)
        if policy is None:
            raise ValueError(PolicyActivationBlock.POLICY_MISSING)
        if policy.allowed_capabilities and capability not in policy.allowed_capabilities:
            raise ValueError(PolicyActivationBlock.CAPABILITY_BLOCKED)
        if not policy.advisory_only or policy.authority_granted or policy.execution_requested:
            raise ValueError(PolicyActivationBlock.AUTHORITY_VIOLATION)
        return {
            "policy_id": policy.policy_id, "version": policy.version,
            "policy_fingerprint": policy.policy_fingerprint,
            "binding_id": binding.binding_id, "change_id": binding.change_id,
            "binding_fingerprint": binding.binding_fingerprint,
            "capability": str(capability), "advisory_only": True,
            "authority_granted": False, "execution_requested": False,
        }

    def close(self) -> None:
        self._db.close()


__all__ = ["PolicyActivationBlock", "PolicyActivationBinding", "WorkerSelectionPolicyActivation"]
