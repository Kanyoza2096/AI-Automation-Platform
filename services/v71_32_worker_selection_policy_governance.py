"""Phase 7.21 — Worker Selection Policy Change Governance & Approval.

Governance lifecycle for selection policies. This subsystem governs policy state;
it does not authorize worker actions, execute work, call providers, or invoke the
Action Kernel. Every transition is durable, workspace-scoped, fingerprinted, and
actor-attributed.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Mapping, Any
import hashlib, json, sqlite3


class PolicyGovernanceBlock(str):
    TENANT_MISMATCH = "tenant_mismatch"
    POLICY_MISSING = "policy_missing"
    INVALID_TRANSITION = "invalid_transition"
    ROLE_REQUIRED = "role_required"
    DUPLICATE = "duplicate"
    TAMPERED = "tampered"
    ACTIVE_CONFLICT = "active_conflict"
    INVALID_POLICY = "invalid_policy"
    AUTHORITY_VIOLATION = "authority_violation"


@dataclass(frozen=True)
class PolicyChange:
    change_id: str
    workspace_id: str
    policy_id: str
    version: int
    requested_by: str
    policy_fingerprint: str
    state: str = "PROPOSED"
    reviewed_by: str = ""
    approved_by: str = ""
    activated_by: str = ""
    supersedes_version: Optional[int] = None
    rollback_of_version: Optional[int] = None
    change_fingerprint: str = ""

    def payload(self) -> dict[str, Any]:
        return {
            "change_id": self.change_id, "workspace_id": self.workspace_id,
            "policy_id": self.policy_id, "version": self.version,
            "requested_by": self.requested_by, "policy_fingerprint": self.policy_fingerprint,
            "state": self.state, "reviewed_by": self.reviewed_by,
            "approved_by": self.approved_by, "activated_by": self.activated_by,
            "supersedes_version": self.supersedes_version,
            "rollback_of_version": self.rollback_of_version,
        }

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.payload(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class WorkerSelectionPolicyGovernance:
    VERSION = "phase7.21"
    STATES = ("PROPOSED", "REVIEWED", "APPROVED", "ACTIVE", "SUPERSEDED", "ROLLED_BACK")

    def __init__(self, workspace_id: str, db_path: str = ":memory:"):
        ws = str(workspace_id or "").strip()
        if not ws:
            raise ValueError(PolicyGovernanceBlock.TENANT_MISMATCH)
        self.workspace_id = ws
        self._db = sqlite3.connect(db_path)
        self._db.execute("""CREATE TABLE IF NOT EXISTS worker_selection_policy_changes (
            change_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, policy_id TEXT NOT NULL,
            version INTEGER NOT NULL, requested_by TEXT NOT NULL, policy_fingerprint TEXT NOT NULL,
            state TEXT NOT NULL, reviewed_by TEXT NOT NULL, approved_by TEXT NOT NULL,
            activated_by TEXT NOT NULL, supersedes_version INTEGER, rollback_of_version INTEGER,
            change_fingerprint TEXT NOT NULL
        )""")
        self._db.execute("""CREATE TABLE IF NOT EXISTS worker_selection_policy_audit (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT, workspace_id TEXT NOT NULL,
            change_id TEXT NOT NULL, policy_id TEXT NOT NULL, version INTEGER NOT NULL,
            from_state TEXT NOT NULL, to_state TEXT NOT NULL, actor TEXT NOT NULL,
            role TEXT NOT NULL, policy_fingerprint TEXT NOT NULL, event_fingerprint TEXT NOT NULL
        )""")
        self._db.commit()

    def _require(self, actor: str, role: str) -> None:
        actor = str(actor or "").strip()
        role = str(role or "").strip()
        allowed = {
            "PROPOSED": {"policy_owner", "policy_admin"},
            "REVIEWED": {"policy_reviewer", "policy_admin"},
            "APPROVED": {"policy_approver", "policy_admin"},
            "ACTIVE": {"policy_approver", "policy_admin"},
            "SUPERSEDED": {"policy_admin", "policy_approver"},
            "ROLLED_BACK": {"policy_admin", "policy_approver"},
        }
        if not actor or role not in allowed.get(self._target_state, set()):
            raise ValueError(PolicyGovernanceBlock.ROLE_REQUIRED)

    def _row(self, change_id: str) -> Optional[PolicyChange]:
        r = self._db.execute("SELECT change_id,workspace_id,policy_id,version,requested_by,policy_fingerprint,state,reviewed_by,approved_by,activated_by,supersedes_version,rollback_of_version,change_fingerprint FROM worker_selection_policy_changes WHERE change_id=?", (str(change_id),)).fetchone()
        return PolicyChange(*r) if r else None

    def propose(self, *, change_id: str, policy_id: str, version: int, requested_by: str,
                policy_fingerprint: str, supersedes_version: Optional[int] = None,
                rollback_of_version: Optional[int] = None, role: str = "policy_owner") -> PolicyChange:
        self._target_state = "PROPOSED"; self._require(requested_by, role)
        if not change_id or not policy_id or int(version) < 1 or not policy_fingerprint:
            raise ValueError(PolicyGovernanceBlock.INVALID_POLICY)
        if self._row(change_id):
            raise ValueError(PolicyGovernanceBlock.DUPLICATE)
        c = PolicyChange(change_id, self.workspace_id, policy_id, int(version), requested_by, policy_fingerprint,
                         "PROPOSED", "", "", "", supersedes_version, rollback_of_version, "")
        c = PolicyChange(**{**c.__dict__, "change_fingerprint": c.fingerprint()})
        self._insert(c)
        self._append_audit(c, from_state="", to_state="PROPOSED", actor=requested_by, role=role)
        return c

    def transition(self, change_id: str, *, actor: str, role: str, target: str) -> PolicyChange:
        target = str(target).upper()
        c = self._row(change_id)
        if not c or c.workspace_id != self.workspace_id:
            raise ValueError(PolicyGovernanceBlock.POLICY_MISSING)
        if c.change_fingerprint != c.fingerprint():
            raise ValueError(PolicyGovernanceBlock.TAMPERED)
        allowed = {"PROPOSED": {"REVIEWED"}, "REVIEWED": {"APPROVED"}, "APPROVED": {"ACTIVE"},
                   "ACTIVE": {"SUPERSEDED", "ROLLED_BACK"}}
        if target not in allowed.get(c.state, set()):
            raise ValueError(PolicyGovernanceBlock.INVALID_TRANSITION)
        self._target_state = target; self._require(actor, role)
        # Approval and activation are separate human/control-plane decisions.
        updated = c
        if target == "REVIEWED": updated = PolicyChange(**{**c.__dict__, "state": target, "reviewed_by": actor})
        elif target == "APPROVED": updated = PolicyChange(**{**c.__dict__, "state": target, "approved_by": actor})
        elif target == "ACTIVE":
            conflict = self._db.execute("SELECT 1 FROM worker_selection_policy_changes WHERE policy_id=? AND workspace_id=? AND state='ACTIVE' AND change_id<>?", (c.policy_id,self.workspace_id,c.change_id)).fetchone()
            if conflict: raise ValueError(PolicyGovernanceBlock.ACTIVE_CONFLICT)
            updated = PolicyChange(**{**c.__dict__, "state": target, "activated_by": actor})
        elif target in ("SUPERSEDED", "ROLLED_BACK"):
            updated = PolicyChange(**{**c.__dict__, "state": target})
        updated = PolicyChange(**{**updated.__dict__, "change_fingerprint": ""})
        updated = PolicyChange(**{**updated.__dict__, "change_fingerprint": updated.fingerprint()})
        self._replace(updated)
        self._append_audit(updated, from_state=c.state, to_state=target, actor=actor, role=role)
        return updated

    def assert_integrity(self, change: PolicyChange) -> None:
        if change.workspace_id != self.workspace_id or change.change_fingerprint != change.fingerprint():
            raise ValueError(PolicyGovernanceBlock.TAMPERED)

    def active(self, policy_id: str) -> Optional[PolicyChange]:
        r = self._db.execute("SELECT change_id,workspace_id,policy_id,version,requested_by,policy_fingerprint,state,reviewed_by,approved_by,activated_by,supersedes_version,rollback_of_version,change_fingerprint FROM worker_selection_policy_changes WHERE workspace_id=? AND policy_id=? AND state='ACTIVE' ORDER BY version DESC LIMIT 1", (self.workspace_id,str(policy_id))).fetchone()
        return PolicyChange(*r) if r else None

    def _insert(self, c: PolicyChange) -> None:
        self._db.execute("INSERT INTO worker_selection_policy_changes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", tuple(c.__dict__.values()))
        self._db.commit()

    def _replace(self, c: PolicyChange) -> None:
        self._db.execute("UPDATE worker_selection_policy_changes SET state=?,reviewed_by=?,approved_by=?,activated_by=?,change_fingerprint=? WHERE change_id=?", (c.state,c.reviewed_by,c.approved_by,c.activated_by,c.change_fingerprint,c.change_id))
        self._db.commit()


    def _append_audit(self, change: PolicyChange, *, from_state: str, to_state: str, actor: str, role: str) -> None:
        payload = {
            "workspace_id": self.workspace_id, "change_id": change.change_id,
            "policy_id": change.policy_id, "version": change.version,
            "from_state": from_state, "to_state": to_state, "actor": actor,
            "role": role, "policy_fingerprint": change.policy_fingerprint,
        }
        fp = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self._db.execute(
            "INSERT INTO worker_selection_policy_audit (workspace_id,change_id,policy_id,version,from_state,to_state,actor,role,policy_fingerprint,event_fingerprint) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (self.workspace_id, change.change_id, change.policy_id, change.version, from_state, to_state, actor, role, change.policy_fingerprint, fp),
        )
        self._db.commit()

    def audit_history(self, *, policy_id: Optional[str] = None, change_id: Optional[str] = None) -> list[dict[str, Any]]:
        q = "SELECT event_id,workspace_id,change_id,policy_id,version,from_state,to_state,actor,role,policy_fingerprint,event_fingerprint FROM worker_selection_policy_audit WHERE workspace_id=?"
        args: list[Any] = [self.workspace_id]
        if policy_id is not None:
            q += " AND policy_id=?"; args.append(str(policy_id))
        if change_id is not None:
            q += " AND change_id=?"; args.append(str(change_id))
        q += " ORDER BY event_id ASC"
        cols = ["event_id","workspace_id","change_id","policy_id","version","from_state","to_state","actor","role","policy_fingerprint","event_fingerprint"]
        return [dict(zip(cols, row)) for row in self._db.execute(q, tuple(args)).fetchall()]

    def assert_audit_integrity(self, event: Mapping[str, Any]) -> None:
        payload = {k: event[k] for k in ("workspace_id","change_id","policy_id","version","from_state","to_state","actor","role","policy_fingerprint")}
        expected = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if event.get("workspace_id") != self.workspace_id or event.get("event_fingerprint") != expected:
            raise ValueError(PolicyGovernanceBlock.TAMPERED)

    def close(self) -> None:
        self._db.close()


__all__ = ["PolicyGovernanceBlock", "PolicyChange", "WorkerSelectionPolicyGovernance"]
