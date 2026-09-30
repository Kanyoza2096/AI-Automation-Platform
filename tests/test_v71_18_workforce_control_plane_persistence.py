import pytest
from services.v71_18_workforce_control_plane_persistence import WorkforceControlPlanePersistence
from services.v71_1_agent_identity import AgentIdentityRegistry, AgentStatus
from services.v71_2_agent_capability_permissions import AgentCapabilityPermissionRegistry
from services.v71_3_agent_memory import AgentMemoryRegistry
from services.v71_4_agent_planning import AgentPlanningRegistry
from services.v71_5_agent_evaluation import AgentEvaluationRegistry
from services.v71_6_agent_budgets import AgentBudgetRegistry
from services.v71_7_human_takeover import HumanTakeoverRegistry
from services.v71_8_multi_agent_coordination import MultiAgentCoordinationRegistry
from services.v71_9_bounded_autonomous_operations import BoundedAutonomousOperationsRegistry

class Result:
    def __init__(self, data): self.data=data
class Query:
    def __init__(self, rows): self.rows=rows
    def select(self,*a): return self
    def eq(self,*a): return self
    def execute(self): return Result(self.rows)
class DB:
    def __init__(self, tables): self.tables=tables
    def table(self,name): return Query(self.tables.get(name,[]))

def runtime(ws="w1"):
    i=AgentIdentityRegistry(ws); p=AgentCapabilityPermissionRegistry(ws,i); m=AgentMemoryRegistry(ws,i)
    pl=AgentPlanningRegistry(ws,i,p,m); ev=AgentEvaluationRegistry(ws,i,pl); b=AgentBudgetRegistry(ws,i)
    h=HumanTakeoverRegistry(ws,i); c=MultiAgentCoordinationRegistry(ws,i,p,pl,m,ev,b,h)
    o=BoundedAutonomousOperationsRegistry(ws,i,p,pl,ev,b,h,c)
    return {"agent_identity":i,"agent_permissions":p,"agent_memory":m,"agent_planning":pl,"agent_evaluation":ev,"agent_budgets":b,"human_takeover":h,"multi_agent_coordination":c,"bounded_autonomy":o}

def test_empty_durable_workspace_is_restart_safe():
    r=runtime(); report=WorkforceControlPlanePersistence(workspace_id="w1",db=DB({})).hydrate(r)
    assert report.restart_safe is True
    assert all(v == 0 for v in report.hydrated.values())

def test_tampered_identity_fails_closed():
    r=runtime()
    row={"agent_id":"a","name":"A","purpose":"p","agent_class":"business_worker","version":"1","status":"active","declared_capabilities":[],"metadata":{},"identity_fingerprint":"bad"}
    report=WorkforceControlPlanePersistence(workspace_id="w1",db=DB({"agent_identities":[row]})).hydrate(r)
    assert report.restart_safe is False
    assert any(x.startswith("agent_identity:a:") for x in report.integrity_failures)

def _fp(obj):
    return obj.__class__(**{**obj.__dict__, [k for k in obj.__dict__ if k.endswith('_fingerprint')][0]: obj.fingerprint()})

def test_hydrates_nonempty_control_plane_objects():
    from services.v71_1_agent_identity import AgentIdentity, AgentStatus
    from services.v71_2_agent_capability_permissions import AgentCapabilityPermission, PermissionEffect
    from services.v71_3_agent_memory import AgentMemory, MemoryKind, MemoryStatus
    from services.v71_4_agent_planning import AgentPlan, PlanStep, PlanStatus, PlanDisposition
    from services.v71_5_agent_evaluation import AgentEvaluation, EvaluationMetrics, EvaluationStatus
    from services.v71_6_agent_budgets import AgentBudget, BudgetStatus
    from services.v71_7_human_takeover import HumanTakeoverCase, TakeoverStatus, TakeoverDisposition
    from services.v71_8_multi_agent_coordination import CoordinationRequest, CoordinationTask, CoordinationStatus, CoordinationDisposition
    from services.v71_9_bounded_autonomous_operations import AutonomousRun, AutonomousRunStatus, AutonomousRunDisposition
    import json
    ws='w1'
    i=AgentIdentity('a',ws,'A','p','business_worker','1',AgentStatus.ACTIVE,('crm',),{},''); i=AgentIdentity(**{**i.__dict__,'identity_fingerprint':i.fingerprint()})
    p=AgentCapabilityPermission('p',ws,'a','crm',PermissionEffect.ALLOW,{},'1',''); p=AgentCapabilityPermission(**{**p.__dict__,'permission_fingerprint':p.fingerprint()})
    m=AgentMemory('m',ws,'a',MemoryKind.FACT,'s','c','test',.9,'2026-01-01T00:00:00+00:00',None,'normal',MemoryStatus.ACTIVE,None,{},''); m=AgentMemory(**{**m.__dict__,'memory_fingerprint':m.fingerprint()})
    st=PlanStep('s1','Do','crm','r',{},(),PlanDisposition.EXECUTABLE_CANDIDATE,{})
    pl=AgentPlan('pl',ws,'a','obj',(),(st,),PlanStatus.READY,'1',''); pl=AgentPlan(**{**pl.__dict__,'plan_fingerprint':pl.fingerprint()})
    ev=AgentEvaluation('ev',ws,'a','pl',EvaluationStatus.EVALUATED,EvaluationMetrics(.9,.8,.9,.7,.6),{'x':1},('ok',),'1',''); ev=AgentEvaluation(**{**ev.__dict__,'evaluation_fingerprint':ev.fingerprint()})
    b=AgentBudget('b',ws,'a',10,5.0,3.0,BudgetStatus.ACTIVE,'1',{},2,1.0,.5,''); b=AgentBudget(**{**b.__dict__,'budget_fingerprint':b.fingerprint()})
    h=HumanTakeoverCase('h',ws,'a','why','trigger',{},TakeoverStatus.REQUESTED,None,None,None,'1',''); h=HumanTakeoverCase(**{**h.__dict__,'case_fingerprint':h.fingerprint()})
    ct=CoordinationTask('t','Task','crm',{},'r',(),CoordinationDisposition.ACCEPTED_FOR_GOVERNANCE,{})
    co=CoordinationRequest('co',ws,'a','a2','obj',(ct,),CoordinationStatus.PROPOSED,'1','')
    # Coordination schema forbids self delegation, so use second identity only for this row.
    a2=AgentIdentity('a2',ws,'A2','p','business_worker','1',AgentStatus.ACTIVE,('crm',),{},''); a2=AgentIdentity(**{**a2.__dict__,'identity_fingerprint':a2.fingerprint()})
    co=CoordinationRequest('co',ws,'a','a2','obj',(ct,),CoordinationStatus.PROPOSED,'1',''); co=CoordinationRequest(**{**co.__dict__,'coordination_fingerprint':co.fingerprint()})
    run=AutonomousRun('run',ws,'a','pl','obj','wf','trigger',{'x':1},'b','co',None,AutonomousRunStatus.READY,AutonomousRunDisposition.EXECUTION_CANDIDATE,'1',{},''); run=AutonomousRun(**{**run.__dict__,'run_fingerprint':run.fingerprint()})
    rows={
      'agent_identities':[{**i.__dict__,'status':i.status.value},{**a2.__dict__,'status':a2.status.value}],
      'agent_capability_permissions':[{**p.__dict__,'effect':p.effect.value}],
      'agent_memories':[{**m.__dict__,'kind':m.kind.value,'status':m.status.value}],
      'agent_plans':[{**pl.__dict__,'status':pl.status.value,'constraints':list(pl.constraints),'steps':[s.canonical_payload() for s in pl.steps]}],
      'agent_evaluations':[{**ev.__dict__,'status':ev.status.value,'metrics':ev.metrics.as_dict()}],
      'agent_budgets':[{**b.__dict__,'status':b.status.value}],
      'agent_human_takeover_cases':[{**h.__dict__,'status':h.status.value}],
      'agent_coordination_requests':[{**co.__dict__,'status':co.status.value,'tasks_json':[ct.canonical_payload()]}],
      'agent_autonomous_runs':[{**run.__dict__,'status':run.status.value,'disposition':run.disposition.value,'event_json':dict(run.event),'result_summary_json':dict(run.result_summary)}],
    }
    rr=runtime(ws); report=WorkforceControlPlanePersistence(workspace_id=ws,db=DB(rows)).hydrate(rr)
    assert report.restart_safe is True
    assert all(v==1 or (k=='agent_identities' and v==2) for k,v in report.hydrated.items())
