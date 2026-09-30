"""V69.16 governed multi-step workflow execution plane.
Owns durable orchestration state and execution requests; never performs side effects."""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping
from uuid import uuid4
from services.v69_integration_policy import ApprovalLevel, IntegrationPolicyPlane

class WorkflowState(str, Enum):
    DRAFT='draft'; READY='ready'; RUNNING='running'; WAITING_APPROVAL='waiting_approval'; WAITING_RECONCILIATION='waiting_reconciliation'; COMPLETED='completed'; FAILED='failed'; COMPENSATING='compensating'; COMPENSATED='compensated'; BLOCKED='blocked'
class StepState(str, Enum):
    PENDING='pending'; REQUESTED='requested'; RUNNING='running'; SUCCEEDED='succeeded'; FAILED='failed'; UNCERTAIN='uncertain'; SKIPPED='skipped'; COMPENSATED='compensated'

@dataclass(frozen=True)
class WorkflowStep:
    step_id:str; action:str; capability:str; depends_on:tuple[str,...]=(); compensation_action:str|None=None; compensation_capability:str|None=None; idempotency_key:str|None=None; metadata:Mapping[str,object]=field(default_factory=dict)
    def __post_init__(self):
        if not self.step_id.strip() or not self.action.strip() or not self.capability.strip(): raise ValueError('invalid_workflow_step')
        if self.step_id in self.depends_on: raise ValueError('workflow_self_dependency')

@dataclass(frozen=True)
class WorkflowDefinition:
    workflow_id:str; workspace_id:str; name:str; steps:tuple[WorkflowStep,...]; version:str='1.0'
    def __post_init__(self):
        ids=[s.step_id for s in self.steps]
        if len(ids)!=len(set(ids)): raise ValueError('duplicate_workflow_step')
        known=set(ids)
        if any(d not in known for s in self.steps for d in s.depends_on): raise ValueError('unknown_workflow_dependency')
        graph={s.step_id:set(s.depends_on) for s in self.steps}; resolved=set()
        while graph:
            ready={k for k,v in graph.items() if not v or v<=resolved}
            if not ready: raise ValueError('workflow_dependency_cycle')
            resolved|=ready
            for k in ready: graph.pop(k)

@dataclass(frozen=True)
class StepRuntime:
    step_id:str; state:StepState=StepState.PENDING; attempt:int=0; execution_request_id:str|None=None; external_reference:str|None=None; result_fingerprint:str|None=None; reason:str|None=None
@dataclass(frozen=True)
class WorkflowRuntime:
    execution_id:str; definition:WorkflowDefinition; state:WorkflowState; steps:tuple[StepRuntime,...]; correlation_id:str
    def step(self,step_id):
        for s in self.steps:
            if s.step_id==step_id:return s
        raise KeyError(step_id)
@dataclass(frozen=True)
class ExecutionRequest:
    request_id:str; execution_id:str; workspace_id:str; step_id:str; action:str; capability:str; idempotency_key:str; approval_level:ApprovalLevel; authority_granted:bool=False; execution_effect:str='none'
    def __post_init__(self):
        if self.authority_granted or self.execution_effect!='none': raise ValueError('workflow_request_cannot_grant_authority')
@dataclass(frozen=True)
class WorkflowPlan:
    execution_id:str; allowed:bool; state:WorkflowState; ready_steps:tuple[str,...]=(); requests:tuple[ExecutionRequest,...]=(); blocked_steps:tuple[str,...]=(); reasons:tuple[str,...]=()

class WorkflowExecutionPlane:
    def __init__(self,definition,policy):
        if definition.workspace_id!=policy.workspace_id: raise ValueError('tenant_mismatch')
        self.definition=definition; self.policy=policy
    def create(self,correlation_id=None):
        return WorkflowRuntime(str(uuid4()),self.definition,WorkflowState.READY,tuple(StepRuntime(s.step_id) for s in self.definition.steps),correlation_id or str(uuid4()))
    def plan(self,runtime):
        if runtime.definition.workspace_id!=self.policy.workspace_id:return WorkflowPlan(runtime.execution_id,False,WorkflowState.BLOCKED,reasons=('tenant_mismatch',))
        by={s.step_id:s for s in runtime.steps}; ready=[]; blocked=[]; req=[]; reasons=[]
        for spec in self.definition.steps:
            cur=by[spec.step_id]
            if cur.state not in {StepState.PENDING}: continue
            deps=[by[d].state for d in spec.depends_on]
            if any(x in {StepState.FAILED,StepState.UNCERTAIN} for x in deps): blocked.append(spec.step_id); reasons.append(f'dependency_not_successful:{spec.step_id}'); continue
            if not all(x==StepState.SUCCEEDED for x in deps): continue
            dec=self.policy.evaluate(spec.action,capability=spec.capability,context=spec.metadata)
            if not dec.allowed_to_proceed: blocked.append(spec.step_id); reasons.extend(f'{spec.step_id}:{r}' for r in dec.reasons); continue
            if dec.approval.level in {ApprovalLevel.HUMAN,ApprovalLevel.COUNCIL}: blocked.append(spec.step_id); reasons.append(f'{spec.step_id}:approval_required:{dec.approval.level.value}'); continue
            key=spec.idempotency_key or f'{runtime.execution_id}:{spec.step_id}:v{self.definition.version}'
            req.append(ExecutionRequest(str(uuid4()),runtime.execution_id,self.definition.workspace_id,spec.step_id,spec.action,spec.capability,key,dec.approval.level)); ready.append(spec.step_id)
        return WorkflowPlan(runtime.execution_id,bool(req) and not blocked,WorkflowState.RUNNING if req and not blocked else (WorkflowState.WAITING_APPROVAL if blocked else runtime.state),tuple(ready),tuple(req),tuple(blocked),tuple(dict.fromkeys(reasons)))
    def mark_requested(self,runtime,step_id,request):
        if request.execution_id!=runtime.execution_id or request.workspace_id!=self.definition.workspace_id: raise ValueError('execution_request_mismatch')
        runtime.step(step_id)
        return self._replace(runtime,step_id,StepRuntime(step_id,StepState.REQUESTED,0,request.request_id))
    def apply_result(self,runtime,step_id,success,uncertain=False,external_reference=None,result_fingerprint=None,reason=None):
        cur=runtime.step(step_id)
        if cur.state not in {StepState.REQUESTED,StepState.RUNNING}: raise ValueError('invalid_step_result_transition')
        if uncertain and success: raise ValueError('result_cannot_be_success_and_uncertain')
        state=StepState.UNCERTAIN if uncertain else (StepState.SUCCEEDED if success else StepState.FAILED)
        u=self._replace(runtime,step_id,StepRuntime(step_id,state,cur.attempt+1,cur.execution_request_id,external_reference,result_fingerprint,reason))
        if any(s.state==StepState.UNCERTAIN for s in u.steps): return WorkflowRuntime(u.execution_id,u.definition,WorkflowState.WAITING_RECONCILIATION,u.steps,u.correlation_id)
        if all(s.state in {StepState.SUCCEEDED,StepState.SKIPPED,StepState.COMPENSATED} for s in u.steps): return WorkflowRuntime(u.execution_id,u.definition,WorkflowState.COMPLETED,u.steps,u.correlation_id)
        if any(s.state==StepState.FAILED for s in u.steps): return WorkflowRuntime(u.execution_id,u.definition,WorkflowState.FAILED,u.steps,u.correlation_id)
        return u
    def compensation_plan(self,runtime):
        if runtime.state not in {WorkflowState.FAILED,WorkflowState.WAITING_RECONCILIATION}: return ()
        out=[]
        for spec in reversed(self.definition.steps):
            cur=runtime.step(spec.step_id)
            if cur.state!=StepState.SUCCEEDED or not spec.compensation_action: continue
            cap=spec.compensation_capability or spec.compensation_action; dec=self.policy.evaluate(spec.compensation_action,capability=cap)
            if dec.allowed_to_proceed and dec.approval.level==ApprovalLevel.NONE: out.append(ExecutionRequest(str(uuid4()),runtime.execution_id,self.definition.workspace_id,spec.step_id,spec.compensation_action,cap,f'{runtime.execution_id}:{spec.step_id}:compensate',dec.approval.level))
        return tuple(out)
    @staticmethod
    def _replace(runtime,step_id,replacement): return WorkflowRuntime(runtime.execution_id,runtime.definition,runtime.state,tuple(replacement if s.step_id==step_id else s for s in runtime.steps),runtime.correlation_id)
