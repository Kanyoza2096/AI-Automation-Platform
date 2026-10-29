from datetime import datetime, timedelta, timezone
from services.v69_business_process_orchestration import *
from services.v69_business_event_fabric import BusinessEvent

def definition():
    return BusinessProcessDefinition('sales','w1','Lead process',ProcessState.RUNNABLE,transitions=(
        ProcessTransition('t1',ProcessState.RUNNABLE,ProcessState.WAITING_EVENT,'event','lead.created'),
        ProcessTransition('t2',ProcessState.WAITING_EVENT,ProcessState.WAITING_HUMAN,'event','lead.qualified',timeout_seconds=30,escalation_target='sales_manager'),
        ProcessTransition('t3',ProcessState.WAITING_HUMAN,ProcessState.COMPLETED,'human_approve'),
        ProcessTransition('t4',ProcessState.WAITING_HUMAN,ProcessState.FAILED,'human_reject'),
        ProcessTransition('t5',ProcessState.WAITING_TIMER,ProcessState.ESCALATED,'timer'),
    ))

def ev(i,t): return BusinessEvent(i,'w1',t,'crm',{},'c1')

def test_event_driven_long_running_process():
    o=BusinessProcessOrchestrator('w1'); i=o.start(definition()); r=o.on_event(i,ev('e1','lead.created')); assert r.accepted and r.instance.state==ProcessState.WAITING_EVENT
    r=o.on_event(r.instance,ev('e2','lead.qualified')); assert r.accepted and r.instance.state==ProcessState.WAITING_HUMAN and r.instance.deadline

def test_human_gate_and_actor_lineage():
    o=BusinessProcessOrchestrator('w1'); i=o.start(definition()); i=o.on_event(i,ev('e1','lead.created')).instance; i=o.on_event(i,ev('e2','lead.qualified')).instance
    r=o.human_approve(i,actor='manager-1'); assert r.accepted and r.instance.state==ProcessState.COMPLETED and 'actor:manager-1' in r.instance.history

def test_timer_due_and_not_due():
    d=BusinessProcessDefinition('p','w1','P',ProcessState.WAITING_TIMER,transitions=(ProcessTransition('t',ProcessState.WAITING_TIMER,ProcessState.COMPLETED,'timer'),))
    o=BusinessProcessOrchestrator('w1'); i=o.start(d); i=ProcessInstance(i.instance_id,i.definition,ProcessState.WAITING_TIMER,i.correlation_id,deadline=datetime.now(timezone.utc)+timedelta(seconds=60))
    assert not o.tick(i).accepted; i=ProcessInstance(i.instance_id,i.definition,ProcessState.WAITING_TIMER,i.correlation_id,deadline=datetime.now(timezone.utc)-timedelta(seconds=1)); assert o.tick(i).accepted

def test_duplicate_event_is_rejected():
    o=BusinessProcessOrchestrator('w1'); i=o.start(definition()); e=ev('e1','lead.created'); i=o.on_event(i,e).instance; r=o.on_event(i,e); assert not r.accepted and 'duplicate_event' in r.reason_codes

def test_cross_tenant_fails_closed():
    o=BusinessProcessOrchestrator('w1'); i=o.start(definition()); e=BusinessEvent('e','w2','lead.created','crm',{},'c'); assert not o.on_event(i,e).accepted

def test_escalation_requires_target():
    o=BusinessProcessOrchestrator('w1'); i=o.start(definition()); i=o.on_event(i,ev('e1','lead.created')).instance; i=o.on_event(i,ev('e2','lead.qualified')).instance; r=o.escalate(i); assert r.accepted and r.instance.state==ProcessState.ESCALATED
