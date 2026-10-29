"""Phase 7.20 — Worker Selection Policy & Constraint Profiles.

Versioned, workspace-scoped selection policy is durable evidence/configuration.
It constrains the canonical selector; it never grants authority or executes work.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Mapping, Optional
import hashlib, json, sqlite3


class SelectionPolicyBlock(str):
    TENANT_MISMATCH = "tenant_mismatch"
    INVALID_POLICY = "invalid_policy"
    DUPLICATE = "duplicate"
    POLICY_MISSING = "policy_missing"
    POLICY_TAMPERED = "policy_tampered"
    POLICY_IMMUTABLE = "policy_immutable"
    AUTHORITY_VIOLATION = "authority_violation"


@dataclass(frozen=True)
class WorkerSelectionPolicyProfile:
    policy_id: str
    workspace_id: str
    version: int
    capability_weight: float = 0.30
    capacity_weight: float = 0.30
    safety_weight: float = 0.20
    autonomy_weight: float = 0.10
    budget_weight: float = 0.10
    minimum_autonomy_level: int = 0
    minimum_capacity_score: float = 0.0
    minimum_safety_score: float = 0.0
    reputation_influence: float = 0.05
    minimum_reputation_samples: int = 5
    require_verified_reputation_samples: bool = True
    allowed_capabilities: tuple[str, ...] = ()
    advisory_only: bool = True
    authority_granted: bool = False
    execution_requested: bool = False
    policy_fingerprint: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id, "workspace_id": self.workspace_id,
            "version": self.version, "capability_weight": self.capability_weight,
            "capacity_weight": self.capacity_weight, "safety_weight": self.safety_weight,
            "autonomy_weight": self.autonomy_weight, "budget_weight": self.budget_weight,
            "minimum_autonomy_level": self.minimum_autonomy_level,
            "minimum_capacity_score": self.minimum_capacity_score,
            "minimum_safety_score": self.minimum_safety_score,
            "reputation_influence": self.reputation_influence,
            "minimum_reputation_samples": self.minimum_reputation_samples,
            "require_verified_reputation_samples": self.require_verified_reputation_samples,
            "allowed_capabilities": list(self.allowed_capabilities),
            "advisory_only": self.advisory_only,
            "authority_granted": self.authority_granted,
            "execution_requested": self.execution_requested,
        }

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()

    def validate(self) -> None:
        vals = (self.capability_weight, self.capacity_weight, self.safety_weight, self.autonomy_weight, self.budget_weight)
        if any(float(v) < 0 or float(v) > 1 for v in vals) or abs(sum(vals) - 1.0) > 1e-9:
            raise ValueError(SelectionPolicyBlock.INVALID_POLICY)
        if not 0 <= int(self.minimum_autonomy_level) <= 5:
            raise ValueError(SelectionPolicyBlock.INVALID_POLICY)
        if not 0 <= float(self.minimum_capacity_score) <= 1 or not 0 <= float(self.minimum_safety_score) <= 1:
            raise ValueError(SelectionPolicyBlock.INVALID_POLICY)
        if not 0 <= float(self.reputation_influence) <= 0.10 or int(self.minimum_reputation_samples) < 1:
            raise ValueError(SelectionPolicyBlock.INVALID_POLICY)
        if int(self.version) < 1 or not str(self.policy_id).strip() or not str(self.workspace_id).strip():
            raise ValueError(SelectionPolicyBlock.INVALID_POLICY)
        if not self.advisory_only or self.authority_granted or self.execution_requested:
            raise ValueError(SelectionPolicyBlock.AUTHORITY_VIOLATION)


class WorkerSelectionPolicyRegistry:
    VERSION = "phase7.20"

    def __init__(self, workspace_id: str, db_path: str = ":memory:"):
        ws = str(workspace_id or "").strip()
        if not ws:
            raise ValueError(SelectionPolicyBlock.TENANT_MISMATCH)
        self.workspace_id = ws
        self._db = sqlite3.connect(db_path)
        self._db.execute("""CREATE TABLE IF NOT EXISTS worker_selection_policies (
            policy_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            payload TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            PRIMARY KEY(policy_id, version)
        )""")
        self._db.commit()

    def publish(self, profile: WorkerSelectionPolicyProfile) -> WorkerSelectionPolicyProfile:
        if profile.workspace_id != self.workspace_id:
            raise ValueError(SelectionPolicyBlock.TENANT_MISMATCH)
        profile.validate()
        if profile.policy_fingerprint and profile.policy_fingerprint != profile.fingerprint():
            raise ValueError(SelectionPolicyBlock.POLICY_TAMPERED)
        existing = self._db.execute("SELECT 1 FROM worker_selection_policies WHERE policy_id=? AND version=?", (profile.policy_id, profile.version)).fetchone()
        if existing:
            raise ValueError(SelectionPolicyBlock.DUPLICATE)
        record = WorkerSelectionPolicyProfile(**{**profile.__dict__, "policy_fingerprint": profile.fingerprint()})
        self._db.execute("INSERT INTO worker_selection_policies VALUES (?,?,?,?,?)", (record.policy_id, record.workspace_id, record.version, json.dumps(record.canonical_payload(), sort_keys=True), record.policy_fingerprint))
        self._db.commit()
        return record

    def get(self, policy_id: str, version: Optional[int] = None) -> Optional[WorkerSelectionPolicyProfile]:
        if version is None:
            row = self._db.execute("SELECT payload,fingerprint FROM worker_selection_policies WHERE policy_id=? ORDER BY version DESC LIMIT 1", (str(policy_id),)).fetchone()
        else:
            row = self._db.execute("SELECT payload,fingerprint FROM worker_selection_policies WHERE policy_id=? AND version=?", (str(policy_id), int(version))).fetchone()
        if not row:
            return None
        p = json.loads(row[0])
        p["allowed_capabilities"] = tuple(p.get("allowed_capabilities", ()))
        p["policy_fingerprint"] = row[1]
        return WorkerSelectionPolicyProfile(**p)

    def assert_integrity(self, profile: WorkerSelectionPolicyProfile) -> None:
        if profile.workspace_id != self.workspace_id:
            raise ValueError(SelectionPolicyBlock.TENANT_MISMATCH)
        if profile.policy_fingerprint != profile.fingerprint():
            raise ValueError(SelectionPolicyBlock.POLICY_TAMPERED)
        profile.validate()
        stored = self.get(profile.policy_id, profile.version)
        if stored is None or stored.policy_fingerprint != profile.policy_fingerprint:
            raise ValueError(SelectionPolicyBlock.POLICY_TAMPERED)

    def context(self, policy_id: str, version: Optional[int] = None) -> Mapping[str, Any]:
        profile = self.get(policy_id, version)
        if profile is None:
            raise ValueError(SelectionPolicyBlock.POLICY_MISSING)
        self.assert_integrity(profile)
        return {"policy_id": profile.policy_id, "version": profile.version, "policy_fingerprint": profile.policy_fingerprint,
                "advisory_only": True, "authority_granted": False, "execution_requested": False}

    def close(self) -> None:
        self._db.close()


__all__ = ["SelectionPolicyBlock", "WorkerSelectionPolicyProfile", "WorkerSelectionPolicyRegistry"]
