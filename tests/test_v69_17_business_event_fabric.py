from services.v69_business_event_fabric import *
from services.v69_integration_evidence import MemoryEvidenceStore

def event(**kw):
    return BusinessEvent(kw.get('event_id','e1'),'w1',kw.get('event_type','lead.created'),'crm',{'x':1},'c1',sequence=kw.get('sequence'))

def test_publish_routes_only_matching_tenant_and_type():
    f=MemoryEventFabric('w1'); f.subscribe(EventSubscription('s1','w1','lead.created','workflow:1')); f.subscribe(EventSubscription('s2','w1','other','workflow:2'))
    r=f.publish(event()); assert r.accepted and len(r.deliveries)==1 and r.deliveries[0].destination=='workflow:1'

def test_duplicate_is_idempotently_rejected_not_redelivered():
    f=MemoryEventFabric('w1'); f.subscribe(EventSubscription('s1','w1','lead.created','workflow:1')); assert f.publish(event()).accepted; r=f.publish(event()); assert not r.accepted and 'duplicate_event' in r.reasons

def test_cross_tenant_rejected():
    f=MemoryEventFabric('w1')
    r=f.publish(BusinessEvent('e2','w2','lead.created','crm',{},'c2')); assert not r.accepted and 'tenant_mismatch' in r.reasons

def test_loop_and_hop_guard():
    f=MemoryEventFabric('w1'); r=f.publish(BusinessEvent('e2','w1','x','crm',{},'c2',trace=('crm',))); assert not r.accepted

def test_sequence_monotonicity():
    f=MemoryEventFabric('w1'); assert f.publish(event(sequence=2)).accepted; r=f.publish(BusinessEvent('e2','w1','lead.created','crm',{},'c1',sequence=1)); assert not r.accepted and 'ordering_conflict' in r.reasons

def test_delivery_state_and_tenant():
    f=MemoryEventFabric('w1'); f.subscribe(EventSubscription('s1','w1','lead.created','workflow:1')); r=f.publish(event()); d=f.mark_delivery('e1','s1','DELIVERED',attempt=1); assert d.state=='DELIVERED'

def test_evidence_emitted():
    sink=MemoryEvidenceStore(); f=MemoryEventFabric('w1',evidence_sink=sink); f.publish(event()); assert sink.list('w1')
