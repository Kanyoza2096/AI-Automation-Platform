from datetime import datetime,timezone
from services.business_integration_platform import AutonomyMode,ConnectionState,ConnectorContract,IntegrationConnection
from services.v69_connector_activation_registry import ConnectorRegistryRecord,RegistryState,CertificationEvidence
from services.v69_integration_policy import IntegrationPolicyPlane,ApprovalLevel
from services.v69_integration_workflow_execution import *
def policy(action='generate_draft'):
 c=ConnectorContract('facebook','meta','1.0',{action},set(),{action},set(),{action:'medium'},True,True,True); n=IntegrationConnection('w1','facebook',ConnectionState.EXECUTION_ENABLED,{action},AutonomyMode.EXECUTE,'c1'); e=CertificationEvidence('1','facebook','1.0','fp','PASS',True,datetime.now(timezone.utc),None,'ref'); r=ConnectorRegistryRecord('w1','c1','facebook','1.0',RegistryState.ACTIVE,e,1,'ok'); return IntegrationPolicyPlane('w1',n,c,r)
def wf(): return WorkflowDefinition('wf','w1','x',(WorkflowStep('a','generate_draft','generate_draft'),WorkflowStep('b','generate_draft','generate_draft',('a',))))
def test_dependency_order():
 x=WorkflowExecutionPlane(wf(),policy()); r=x.create(); q=x.plan(r); assert q.ready_steps==('a',); r=x.mark_requested(r,'a',q.requests[0]); r=x.apply_result(r,'a',True); assert x.plan(r).ready_steps==('b',)
def test_cycle():
 try: WorkflowDefinition('wf','w1','x',(WorkflowStep('a','generate_draft','generate_draft',('b',)),WorkflowStep('b','generate_draft','generate_draft',('a',))))
 except ValueError as e: assert str(e)=='workflow_dependency_cycle'
 else: assert False
def test_tenant():
 try: WorkflowExecutionPlane(WorkflowDefinition('wf','w2','x',(WorkflowStep('a','generate_draft','generate_draft'),)),policy())
 except ValueError as e: assert str(e)=='tenant_mismatch'
 else: assert False
def test_failure_blocks_dependency():
 x=WorkflowExecutionPlane(wf(),policy()); r=x.create(); q=x.plan(r); r=x.mark_requested(r,'a',q.requests[0]); r=x.apply_result(r,'a',False); assert 'b' in x.plan(r).blocked_steps
def test_uncertain_reconciliation():
 x=WorkflowExecutionPlane(WorkflowDefinition('wf','w1','x',(WorkflowStep('a','generate_draft','generate_draft'),)),policy()); r=x.create(); q=x.plan(r); r=x.mark_requested(r,'a',q.requests[0]); r=x.apply_result(r,'a',False,True); assert r.state==WorkflowState.WAITING_RECONCILIATION
def test_success_complete():
 x=WorkflowExecutionPlane(WorkflowDefinition('wf','w1','x',(WorkflowStep('a','generate_draft','generate_draft'),)),policy()); r=x.create(); q=x.plan(r); r=x.mark_requested(r,'a',q.requests[0]); assert x.apply_result(r,'a',True).state==WorkflowState.COMPLETED
def test_request_no_authority():
 try: ExecutionRequest('r','e','w1','a','generate_draft','generate_draft','k',ApprovalLevel.NONE,True)
 except ValueError as e: assert str(e)=='workflow_request_cannot_grant_authority'
 else: assert False
def test_duplicate_steps():
 try: WorkflowDefinition('wf','w1','x',(WorkflowStep('a','generate_draft','generate_draft'),WorkflowStep('a','generate_draft','generate_draft')))
 except ValueError as e: assert str(e)=='duplicate_workflow_step'
 else: assert False
def test_compensation_requires_policy():
 d=WorkflowDefinition('wf','w1','x',(WorkflowStep('a','generate_draft','generate_draft',compensation_action='generate_draft',compensation_capability='generate_draft'),)); x=WorkflowExecutionPlane(d,policy()); r=x.create(); q=x.plan(r); r=x.mark_requested(r,'a',q.requests[0]); r=x.apply_result(r,'a',True); assert x.compensation_plan(r)==()
