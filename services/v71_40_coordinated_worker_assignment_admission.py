"""Phase 7.29 — Coordinated Worker Assignment Admission.

Transforms one execution-ready multi-worker coordination record into canonical
worker assignments through the existing V71.25 admission boundary. The service
coordinates admission only: it never grants authorization, executes work,
invokes providers, consumes runtime capacity, or invokes the Action Kernel.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any
import hashlib, json, sqlite3

from services.v71_37_workforce_multi_worker_planning import WorkforceMultiWorkerPlanner
from services.v71_39_workforce_execution_readiness import WorkforceExecutionReadinessRegistry, ReadinessDisposition
from services.v71_25_worker_assignment import WorkerAssignmentAdmission, WorkerAssignment

class CoordinatedAdmissionBlock(str, Enum):
    TENANT_MISMATCH="tenant_mismatch"
    READINESS_MISSING="readiness_missing"
    READINESS_INVALID="readiness_invalid"
    NOT_READY="not_ready"
    DUPLICATE="duplicate"
    TASK_SET_MISMATCH="task_set_mismatch"
    ASSIGNMENT_TAMPERED="assignment_tampered"
    AUTHORITY_VIOLATION="authority_violation"

@dataclass(frozen=True)
class CoordinatedAssignmentAdmission:
    admission_id:str
    workspace_id:str
    readiness_id:str
    plan_id:str
    assignment_ids:tuple[str,...]
    task_count:int
    admitted:bool=True
    coordination_only:bool=True
    authorization_granted:bool=False
    execution_requested:bool=False
    action_kernel_invoked:bool=False
    admission_fingerprint:str=""
    def canonical_payload(self)->dict[str,Any]:
        return {"admission_id":self.admission_id,"workspace_id":self.workspace_id,"readiness_id":self.readiness_id,
                "plan_id":self.plan_id,"assignment_ids":list(self.assignment_ids),"task_count":self.task_count,
                "admitted":self.admitted,"coordination_only":self.coordination_only,
                "authorization_granted":self.authorization_granted,"execution_requested":self.execution_requested,
                "action_kernel_invoked":self.action_kernel_invoked}
    def fingerprint(self)->str:
        return hashlib.sha256(json.dumps(self.canonical_payload(),sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()

class CoordinatedWorkerAssignmentAdmission:
    VERSION="phase7.29"
    def __init__(self, workspace_id:str, readiness:WorkforceExecutionReadinessRegistry,
                 assignment_admission:WorkerAssignmentAdmission, planner:WorkforceMultiWorkerPlanner,
                 db_path:str=":memory:"):
        ws=str(workspace_id or '').strip()
        if not ws or any(x.workspace_id!=ws for x in (readiness,assignment_admission,planner)):
            raise ValueError(CoordinatedAdmissionBlock.TENANT_MISMATCH.value)
        self.workspace_id=ws; self.readiness=readiness; self.assignment_admission=assignment_admission; self.planner=planner
        self._db=sqlite3.connect(db_path)
        self._db.execute('''CREATE TABLE IF NOT EXISTS coordinated_worker_assignment_admissions(
            admission_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, payload TEXT NOT NULL, fingerprint TEXT NOT NULL,
            UNIQUE(workspace_id, admission_id))''')
        self._db.commit()

    def admit(self, *, admission_id:str, readiness_id:str)->CoordinatedAssignmentAdmission:
        aid=str(admission_id or '').strip()
        if not aid: raise ValueError(CoordinatedAdmissionBlock.READINESS_MISSING.value)
        if self.get(aid) is not None: raise ValueError(CoordinatedAdmissionBlock.DUPLICATE.value)
        record=self.readiness.get(readiness_id)
        if record is None: raise ValueError(CoordinatedAdmissionBlock.READINESS_MISSING.value)
        self.readiness.assert_integrity(record)
        if record.disposition is not ReadinessDisposition.READY: raise ValueError(CoordinatedAdmissionBlock.NOT_READY.value)
        plan=self.planner.get(record.plan_id)
        if plan is None: raise ValueError(CoordinatedAdmissionBlock.READINESS_INVALID.value)
        self.planner.assert_integrity(plan)
        if len(record.tasks)!=len(plan.tasks) or {x.task_id for x in record.tasks}!={x.task_id for x in plan.tasks}:
            raise ValueError(CoordinatedAdmissionBlock.TASK_SET_MISMATCH.value)
        task_map={x.task_id:x for x in record.tasks}
        requests=[]
        for task in sorted(plan.tasks,key=lambda x:x.task_id):
            ready=task_map[task.task_id]
            requests.append({"assignment_id":f"{aid}:{task.task_id}","task_id":task.task_id,
                             "selection_id":ready.selection_id,"reservation_id":ready.reservation_id})
        assignments=self.assignment_admission.assign_many(tuple(requests))
        result=CoordinatedAssignmentAdmission(aid,self.workspace_id,record.readiness_id,plan.plan_id,
                                               tuple(x.assignment_id for x in assignments),len(assignments))
        result=CoordinatedAssignmentAdmission(**{**result.__dict__,"admission_fingerprint":result.fingerprint()})
        self._db.execute('INSERT INTO coordinated_worker_assignment_admissions VALUES (?,?,?,?)',
                         (result.admission_id,result.workspace_id,json.dumps(result.canonical_payload(),sort_keys=True),result.admission_fingerprint))
        self._db.commit()
        return result

    def get(self, admission_id:str)->CoordinatedAssignmentAdmission|None:
        row=self._db.execute('SELECT payload,fingerprint FROM coordinated_worker_assignment_admissions WHERE admission_id=? AND workspace_id=?',(str(admission_id),self.workspace_id)).fetchone()
        if not row:return None
        p=json.loads(row[0]); p['assignment_ids']=tuple(p.get('assignment_ids',())); p['admission_fingerprint']=row[1]
        return CoordinatedAssignmentAdmission(**p)

    def assert_integrity(self, admission:CoordinatedAssignmentAdmission)->None:
        if admission.workspace_id!=self.workspace_id: raise ValueError(CoordinatedAdmissionBlock.TENANT_MISMATCH.value)
        if not admission.coordination_only or admission.authorization_granted or admission.execution_requested or admission.action_kernel_invoked:
            raise ValueError(CoordinatedAdmissionBlock.AUTHORITY_VIOLATION.value)
        if admission.admission_fingerprint!=admission.fingerprint(): raise ValueError(CoordinatedAdmissionBlock.ASSIGNMENT_TAMPERED.value)
        stored=self.get(admission.admission_id)
        if stored is None or stored.admission_fingerprint!=admission.admission_fingerprint: raise ValueError(CoordinatedAdmissionBlock.ASSIGNMENT_TAMPERED.value)
        if len(admission.assignment_ids)!=admission.task_count: raise ValueError(CoordinatedAdmissionBlock.ASSIGNMENT_TAMPERED.value)
        for assignment_id in admission.assignment_ids:
            assignment=self.assignment_admission.get(assignment_id)
            if assignment is None: raise ValueError(CoordinatedAdmissionBlock.ASSIGNMENT_TAMPERED.value)
            self.assignment_admission.assert_integrity(assignment)

    def assignments(self, admission_id:str)->tuple[WorkerAssignment,...]:
        record=self.get(admission_id)
        if record is None: raise ValueError(CoordinatedAdmissionBlock.READINESS_MISSING.value)
        self.assert_integrity(record)
        return tuple(self.assignment_admission.get(x) for x in record.assignment_ids)

    def context(self, admission_id:str)->dict[str,Any]:
        record=self.get(admission_id)
        if record is None: raise ValueError(CoordinatedAdmissionBlock.READINESS_MISSING.value)
        self.assert_integrity(record)
        return {"admission_id":record.admission_id,"readiness_id":record.readiness_id,"plan_id":record.plan_id,
                "task_count":record.task_count,"coordination_only":True,"authorization_granted":False,
                "execution_requested":False,"action_kernel_invoked":False}

    def close(self): self._db.close()

__all__ = [
    'CoordinatedAdmissionBlock', 'CoordinatedAssignmentAdmission',
    'CoordinatedWorkerAssignmentAdmission',
]
