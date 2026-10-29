"""Phase 7.40 — Workforce Coordination Recovery & Reconciliation Hardening.

Read-only recovery/reconciliation evidence over the canonical governance and
policy-consumption state. This layer detects interrupted/stale/tampered/
divergent state and classifies recovery readiness; it never repairs canonical
truth, authorizes work, executes work, or calls providers/Action Kernel.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import hashlib, json, sqlite3

from services.v71_47_workforce_coordination_policy_governance import WorkforceCoordinationPolicyGovernance
from services.v71_48_workforce_coordination_policy_enforcement import WorkforceCoordinationPolicyEnforcement

class WorkforceRecoveryBlock(str):
    TENANT_MISMATCH="tenant_mismatch"
    TAMPERED="tampered"
    MISSING_CANONICAL="missing_canonical"
    STALE_BINDING="stale_binding"
    ORPHAN_BINDING="orphan_binding"
    AUDIT_DIVERGENCE="audit_divergence"
    DUPLICATE="duplicate"

@dataclass(frozen=True)
class RecoveryFinding:
    finding_id: str; workspace_id: str; assessment_id: str; binding_id: str
    policy_id: str; severity: str; disposition: str; reason: str
    canonical_change_id: str = ""; binding_fingerprint: str = ""
    finding_fingerprint: str = ""
    def canonical_payload(self)->dict[str,Any]:
        return {k:v for k,v in self.__dict__.items() if k!="finding_fingerprint"}
    def fingerprint(self)->str:
        return hashlib.sha256(json.dumps(self.canonical_payload(),sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()

@dataclass(frozen=True)
class ReconciliationReport:
    report_id: str; workspace_id: str; status: str; checked_bindings: int
    healthy: int; degraded: int; findings: tuple[RecoveryFinding,...]
    report_fingerprint: str = ""
    def canonical_payload(self)->dict[str,Any]:
        return {k:v for k,v in self.__dict__.items() if k!="report_fingerprint"}
    def fingerprint(self)->str:
        payload=self.canonical_payload()
        payload["findings"]=[f.__dict__ for f in self.findings]
        return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()

class WorkforceCoordinationRecoveryReconciler:
    VERSION="phase7.40"
    def __init__(self, workspace_id:str, governance:WorkforceCoordinationPolicyGovernance,
                 enforcement:WorkforceCoordinationPolicyEnforcement, db_path=":memory:"):
        ws=str(workspace_id or "").strip()
        if not ws or governance.workspace_id!=ws or enforcement.workspace_id!=ws:
            raise ValueError(WorkforceRecoveryBlock.TENANT_MISMATCH)
        self.workspace_id=ws; self.governance=governance; self.enforcement=enforcement
        self._db=sqlite3.connect(db_path)
        self._db.execute("""CREATE TABLE IF NOT EXISTS workforce_coordination_recovery_reports(
          report_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, status TEXT NOT NULL,
          checked_bindings INTEGER NOT NULL, healthy INTEGER NOT NULL, degraded INTEGER NOT NULL,
          report_fingerprint TEXT NOT NULL, UNIQUE(workspace_id,report_id))""")
        self._db.commit()

    def _finding(self, *, binding, severity, disposition, reason, canonical_change_id=""):
        f=RecoveryFinding(
            finding_id=hashlib.sha256(f"{self.workspace_id}:{binding.binding_id}:{reason}".encode()).hexdigest()[:24],
            workspace_id=self.workspace_id, assessment_id=binding.assessment_id, binding_id=binding.binding_id,
            policy_id=binding.policy_id, severity=severity, disposition=disposition, reason=reason,
            canonical_change_id=canonical_change_id, binding_fingerprint=binding.binding_fingerprint)
        return RecoveryFinding(**{**f.__dict__,"finding_fingerprint":f.fingerprint()})

    def inspect_binding(self,binding):
        if binding.workspace_id!=self.workspace_id:
            return self._finding(binding=binding,severity="CRITICAL",disposition="RECONCILIATION_REQUIRED",reason=WorkforceRecoveryBlock.TENANT_MISMATCH)
        try:
            self.enforcement.assert_binding_integrity(binding)
            active=self.governance.active(binding.policy_id)
            if active is None:
                return self._finding(binding=binding,severity="HIGH",disposition="RECONCILIATION_REQUIRED",reason=WorkforceRecoveryBlock.MISSING_CANONICAL)
            if active.change_id!=binding.governance_change_id or str(active.version)!=str(binding.policy_version) or active.policy_fingerprint!=binding.policy_fingerprint:
                return self._finding(binding=binding,severity="HIGH",disposition="RECONCILIATION_REQUIRED",reason=WorkforceRecoveryBlock.STALE_BINDING,canonical_change_id=active.change_id)
            return None
        except ValueError as exc:
            reason=str(exc)
            disp="RECONCILIATION_REQUIRED" if reason in {"binding_tampered","policy_tampered","policy_missing","stale_policy"} else "RECOVERY_REVIEW"
            sev="CRITICAL" if "tampered" in reason else "HIGH"
            return self._finding(binding=binding,severity=sev,disposition=disp,reason=reason)

    def reconcile(self, *, report_id:str)->ReconciliationReport:
        if not report_id: raise ValueError(WorkforceRecoveryBlock.DUPLICATE)
        if self._db.execute("SELECT 1 FROM workforce_coordination_recovery_reports WHERE report_id=? AND workspace_id=?",(str(report_id),self.workspace_id)).fetchone():
            raise ValueError(WorkforceRecoveryBlock.DUPLICATE)
        rows=self.enforcement._db.execute("""SELECT binding_id,workspace_id,assessment_id,policy_id,policy_version,
            policy_fingerprint,governance_change_id,governance_state,binding_fingerprint
            FROM workforce_coordination_policy_consumption_bindings WHERE workspace_id=? ORDER BY binding_id""",(self.workspace_id,)).fetchall()
        bindings=[]
        from services.v71_48_workforce_coordination_policy_enforcement import CoordinationPolicyConsumptionBinding
        for row in rows: bindings.append(CoordinationPolicyConsumptionBinding(*row))
        findings=[]
        for b in bindings:
            f=self.inspect_binding(b)
            if f: findings.append(f)
        # Governance audit coverage: every state transition for every known change must exist.
        changes=self.governance._db.execute("SELECT change_id FROM workforce_coordination_policy_changes WHERE workspace_id=?",(self.workspace_id,)).fetchall()
        audited={r[0] for r in self.governance._db.execute("SELECT DISTINCT change_id FROM workforce_coordination_policy_audit WHERE workspace_id=?",(self.workspace_id,)).fetchall()}
        for (cid,) in changes:
            if cid not in audited:
                dummy=type("B",(),{"binding_id":"audit:"+cid,"assessment_id":"","policy_id":"","workspace_id":self.workspace_id,"binding_fingerprint":""})()
                findings.append(self._finding(binding=dummy,severity="HIGH",disposition="RECONCILIATION_REQUIRED",reason=WorkforceRecoveryBlock.AUDIT_DIVERGENCE,canonical_change_id=cid))
        healthy=len(bindings)-len([f for f in findings if f.assessment_id])
        degraded=len(findings)
        status="RECOVERED" if not findings else "RECONCILIATION_REQUIRED"
        report=ReconciliationReport(str(report_id),self.workspace_id,status,len(bindings),max(0,healthy),degraded,tuple(findings))
        report=ReconciliationReport(**{**report.__dict__,"report_fingerprint":report.fingerprint()})
        self._db.execute("INSERT INTO workforce_coordination_recovery_reports VALUES (?,?,?,?,?,?,?)",(report.report_id,report.workspace_id,report.status,report.checked_bindings,report.healthy,report.degraded,report.report_fingerprint));self._db.commit()
        return report

    def assert_report_integrity(self,report:ReconciliationReport):
        if report.workspace_id!=self.workspace_id or report.report_fingerprint!=report.fingerprint(): raise ValueError(WorkforceRecoveryBlock.TAMPERED)
        if not self._db.execute("SELECT 1 FROM workforce_coordination_recovery_reports WHERE report_id=? AND workspace_id=? AND report_fingerprint=?",(report.report_id,self.workspace_id,report.report_fingerprint)).fetchone(): raise ValueError(WorkforceRecoveryBlock.TAMPERED)

    def context(self,report_id:str):
        row=self._db.execute("SELECT report_id,workspace_id,status,checked_bindings,healthy,degraded,report_fingerprint FROM workforce_coordination_recovery_reports WHERE report_id=? AND workspace_id=?",(str(report_id),self.workspace_id)).fetchone()
        if not row: raise ValueError(WorkforceRecoveryBlock.MISSING_CANONICAL)
        return {"report_id":row[0],"workspace_id":row[1],"status":row[2],"checked_bindings":row[3],"healthy":row[4],"degraded":row[5],"report_fingerprint":row[6],"read_only":True,"authority":"none","can_authorize":False,"can_execute":False,"can_call_provider":False,"can_invoke_action_kernel":False}

    def close(self): self._db.close()
