"""Phase 7.36 — Workforce Coordination Policy Governance & Activation.

Governance for adaptive workforce-coordination policies. This layer owns policy
lifecycle evidence only; it never authorizes, executes, provisions, selects,
or invokes the Action Kernel.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Any
import hashlib, json, sqlite3

class CoordinationPolicyGovernanceBlock(str):
    TENANT_MISMATCH="tenant_mismatch"; INVALID_POLICY="invalid_policy"; DUPLICATE="duplicate"
    MISSING="policy_missing"; TAMPERED="tampered"; INVALID_TRANSITION="invalid_transition"
    ROLE_REQUIRED="role_required"; ACTIVE_CONFLICT="active_conflict"
    SEPARATION_OF_DUTIES="separation_of_duties"

@dataclass(frozen=True)
class CoordinationPolicyChange:
    change_id: str; workspace_id: str; policy_id: str; version: int
    requested_by: str; policy_fingerprint: str; state: str
    reviewed_by: str=""; approved_by: str=""; activated_by: str=""
    supersedes_version: Optional[int]=None; rollback_of_version: Optional[int]=None
    change_fingerprint: str=""
    def canonical_payload(self)->dict[str,Any]:
        return {k:v for k,v in self.__dict__.items() if k!="change_fingerprint"}
    def fingerprint(self)->str:
        return hashlib.sha256(json.dumps(self.canonical_payload(),sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()

class WorkforceCoordinationPolicyGovernance:
    VERSION="phase7.36"
    STATES=("PROPOSED","REVIEWED","APPROVED","ACTIVE","SUPERSEDED","ROLLED_BACK")
    def __init__(self, workspace_id:str, db_path:str=":memory:"):
        ws=str(workspace_id or "").strip()
        if not ws: raise ValueError(CoordinationPolicyGovernanceBlock.TENANT_MISMATCH)
        self.workspace_id=ws; self._db=sqlite3.connect(db_path)
        self._db.execute("""CREATE TABLE IF NOT EXISTS workforce_coordination_policy_changes(
          change_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, policy_id TEXT NOT NULL,
          version INTEGER NOT NULL, requested_by TEXT NOT NULL, policy_fingerprint TEXT NOT NULL,
          state TEXT NOT NULL, reviewed_by TEXT NOT NULL, approved_by TEXT NOT NULL,
          activated_by TEXT NOT NULL, supersedes_version INTEGER, rollback_of_version INTEGER,
          change_fingerprint TEXT NOT NULL, UNIQUE(workspace_id,policy_id,version))""")
        self._db.execute("""CREATE TABLE IF NOT EXISTS workforce_coordination_policy_audit(
          event_id INTEGER PRIMARY KEY AUTOINCREMENT, workspace_id TEXT NOT NULL, change_id TEXT NOT NULL,
          policy_id TEXT NOT NULL, version INTEGER NOT NULL, from_state TEXT NOT NULL, to_state TEXT NOT NULL,
          actor TEXT NOT NULL, role TEXT NOT NULL, policy_fingerprint TEXT NOT NULL, event_fingerprint TEXT NOT NULL)""")
        self._db.commit()
    def _get(self,cid):
        r=self._db.execute("SELECT change_id,workspace_id,policy_id,version,requested_by,policy_fingerprint,state,reviewed_by,approved_by,activated_by,supersedes_version,rollback_of_version,change_fingerprint FROM workforce_coordination_policy_changes WHERE change_id=? AND workspace_id=?",(str(cid),self.workspace_id)).fetchone()
        return CoordinationPolicyChange(*r) if r else None
    def _role(self, actor, role, target):
        if not str(actor or "").strip(): raise ValueError(CoordinationPolicyGovernanceBlock.ROLE_REQUIRED)
        allowed={"PROPOSED":{"policy_owner","policy_admin"},"REVIEWED":{"policy_reviewer","policy_admin"},"APPROVED":{"policy_approver","policy_admin"},"ACTIVE":{"policy_approver","policy_admin"},"SUPERSEDED":{"policy_admin","policy_approver"},"ROLLED_BACK":{"policy_admin","policy_approver"}}
        if role not in allowed[target]: raise ValueError(CoordinationPolicyGovernanceBlock.ROLE_REQUIRED)
    def propose(self,*,change_id,policy_id,version,requested_by,policy_fingerprint,supersedes_version=None,rollback_of_version=None,role="policy_owner"):
        self._role(requested_by,role,"PROPOSED")
        if not change_id or not policy_id or int(version)<1 or not policy_fingerprint: raise ValueError(CoordinationPolicyGovernanceBlock.INVALID_POLICY)
        if self._get(change_id): raise ValueError(CoordinationPolicyGovernanceBlock.DUPLICATE)
        c=CoordinationPolicyChange(str(change_id),self.workspace_id,str(policy_id),int(version),str(requested_by),str(policy_fingerprint),"PROPOSED",supersedes_version=supersedes_version,rollback_of_version=rollback_of_version)
        c=CoordinationPolicyChange(**{**c.__dict__,"change_fingerprint":c.fingerprint()})
        try: self._db.execute("INSERT INTO workforce_coordination_policy_changes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",tuple(c.__dict__.values())); self._db.commit()
        except sqlite3.IntegrityError: raise ValueError(CoordinationPolicyGovernanceBlock.DUPLICATE)
        self._audit(c,"","PROPOSED",requested_by,role); return c
    def transition(self,change_id,*,actor,role,target):
        target=str(target).upper(); c=self._get(change_id)
        if not c: raise ValueError(CoordinationPolicyGovernanceBlock.MISSING)
        if c.change_fingerprint!=c.fingerprint(): raise ValueError(CoordinationPolicyGovernanceBlock.TAMPERED)
        allowed={"PROPOSED":{"REVIEWED"},"REVIEWED":{"APPROVED"},"APPROVED":{"ACTIVE"},"ACTIVE":{"SUPERSEDED","ROLLED_BACK"}}
        if target not in allowed.get(c.state,set()): raise ValueError(CoordinationPolicyGovernanceBlock.INVALID_TRANSITION)
        self._role(actor,role,target)
        # Separation of duties: reviewer and approver cannot be the proposer; approver cannot be reviewer.
        if target=="APPROVED" and (actor==c.requested_by or actor==c.reviewed_by): raise ValueError(CoordinationPolicyGovernanceBlock.SEPARATION_OF_DUTIES)
        if target=="ACTIVE" and actor==c.requested_by: raise ValueError(CoordinationPolicyGovernanceBlock.SEPARATION_OF_DUTIES)
        if target=="REVIEWED": n=CoordinationPolicyChange(**{**c.__dict__,"state":target,"reviewed_by":actor})
        elif target=="APPROVED": n=CoordinationPolicyChange(**{**c.__dict__,"state":target,"approved_by":actor})
        elif target=="ACTIVE":
            conflict=self._db.execute("SELECT 1 FROM workforce_coordination_policy_changes WHERE workspace_id=? AND policy_id=? AND state='ACTIVE' AND change_id<>?",(self.workspace_id,c.policy_id,c.change_id)).fetchone()
            if conflict: raise ValueError(CoordinationPolicyGovernanceBlock.ACTIVE_CONFLICT)
            n=CoordinationPolicyChange(**{**c.__dict__,"state":target,"activated_by":actor})
        else: n=CoordinationPolicyChange(**{**c.__dict__,"state":target})
        n=CoordinationPolicyChange(**{**n.__dict__,"change_fingerprint":n.fingerprint()})
        self._db.execute("UPDATE workforce_coordination_policy_changes SET state=?,reviewed_by=?,approved_by=?,activated_by=?,change_fingerprint=? WHERE change_id=?",(n.state,n.reviewed_by,n.approved_by,n.activated_by,n.change_fingerprint,n.change_id)); self._db.commit()
        self._audit(n,c.state,target,actor,role); return n
    def assert_active_binding(self, *, policy_id: str, version: str | int, policy_fingerprint: str) -> CoordinationPolicyChange:
        change = self.active(policy_id)
        if change is None:
            raise ValueError(CoordinationPolicyGovernanceBlock.MISSING)
        self.assert_integrity(change)
        if str(change.version) != str(version) or change.policy_fingerprint != str(policy_fingerprint):
            raise ValueError(CoordinationPolicyGovernanceBlock.TAMPERED)
        return change

    def active(self,policy_id):
        r=self._db.execute("SELECT change_id,workspace_id,policy_id,version,requested_by,policy_fingerprint,state,reviewed_by,approved_by,activated_by,supersedes_version,rollback_of_version,change_fingerprint FROM workforce_coordination_policy_changes WHERE workspace_id=? AND policy_id=? AND state='ACTIVE' ORDER BY version DESC LIMIT 1",(self.workspace_id,str(policy_id))).fetchone()
        return CoordinationPolicyChange(*r) if r else None
    def assert_integrity(self,change):
        if change.workspace_id!=self.workspace_id or change.change_fingerprint!=change.fingerprint(): raise ValueError(CoordinationPolicyGovernanceBlock.TAMPERED)
    def audit_history(self,policy_id=None,change_id=None):
        q="SELECT event_id,workspace_id,change_id,policy_id,version,from_state,to_state,actor,role,policy_fingerprint,event_fingerprint FROM workforce_coordination_policy_audit WHERE workspace_id=?"; a=[self.workspace_id]
        if policy_id is not None:q+=" AND policy_id=?";a.append(str(policy_id))
        if change_id is not None:q+=" AND change_id=?";a.append(str(change_id))
        q+=" ORDER BY event_id"; cols=["event_id","workspace_id","change_id","policy_id","version","from_state","to_state","actor","role","policy_fingerprint","event_fingerprint"]
        return [dict(zip(cols,r)) for r in self._db.execute(q,a).fetchall()]
    def assert_audit_integrity(self,event):
        payload={k:event[k] for k in ("workspace_id","change_id","policy_id","version","from_state","to_state","actor","role","policy_fingerprint")}
        fp=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":")).encode()).hexdigest()
        if event.get("workspace_id")!=self.workspace_id or event.get("event_fingerprint")!=fp: raise ValueError(CoordinationPolicyGovernanceBlock.TAMPERED)
    def _audit(self,c,frm,to,actor,role):
        p={"workspace_id":self.workspace_id,"change_id":c.change_id,"policy_id":c.policy_id,"version":c.version,"from_state":frm,"to_state":to,"actor":actor,"role":role,"policy_fingerprint":c.policy_fingerprint}
        fp=hashlib.sha256(json.dumps(p,sort_keys=True,separators=(",",":")).encode()).hexdigest()
        self._db.execute("INSERT INTO workforce_coordination_policy_audit (workspace_id,change_id,policy_id,version,from_state,to_state,actor,role,policy_fingerprint,event_fingerprint) VALUES (?,?,?,?,?,?,?,?,?,?)",(*p.values(),fp));self._db.commit()
    def close(self): self._db.close()
