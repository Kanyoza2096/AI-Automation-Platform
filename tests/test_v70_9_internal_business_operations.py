import pytest
from datetime import datetime, timezone, timedelta
from services.v70_9_internal_business_operations import *

class FakeLayer:
    def __init__(self, ws): self.workspace_id = ws
    async def handle_event(self, objective_id, workflow_id, event, approved=False, now=None):
        class A: reasons = ()
        class R: accepted = True; admission = A()
        return R()

def loop(): return InternalBusinessOperations('w1', operating_layer=FakeLayer('w1'))

def test_work_lifecycle_and_sla():
    x=loop(); now=datetime(2026,1,1,tzinfo=timezone.utc)
    r=x.process(event={'event_id':'e1','event_name':'work.created','work_id':'w1','title':'Close books','priority':'high'},objective_id='o',now=now)
    assert r.status==WorkStatus.OPEN and r.workflow_id=='operations.internal-attention'
    r=x.process(event={'event_id':'e2','event_name':'work.started','work_id':'w1'},objective_id='o',now=now+timedelta(hours=1))
    assert r.status==WorkStatus.IN_PROGRESS and not r.sla_breached
    r=x.process(event={'event_id':'e3','event_name':'work.sla_breached','work_id':'w1'},objective_id='o',now=now+timedelta(hours=9))
    assert r.sla_breached and r.status==WorkStatus.BLOCKED and r.workflow_id=='operations.sla-breach'

def test_blocked_failed_and_recovery():
    x=loop(); n=datetime(2026,1,1,tzinfo=timezone.utc)
    x.process(event={'event_id':'e1','event_name':'work.created','work_id':'w1'},objective_id='o',now=n)
    r=x.process(event={'event_id':'e2','event_name':'work.blocked','work_id':'w1'},objective_id='o',now=n)
    assert r.workflow_id=='operations.blocked-work'
    r=x.process(event={'event_id':'e3','event_name':'work.resumed','work_id':'w1'},objective_id='o',now=n)
    assert r.status==WorkStatus.IN_PROGRESS
    r=x.process(event={'event_id':'e4','event_name':'work.failed','work_id':'w1'},objective_id='o',now=n)
    assert r.workflow_id=='operations.failed-work'

def test_tenant_duplicate_and_terminal_fences():
    x=loop(); n=datetime(2026,1,1,tzinfo=timezone.utc)
    with pytest.raises(ValueError,match='tenant_mismatch'): x.process(event={'event_id':'e1','event_name':'work.created','work_id':'w1','workspace_id':'w2'},objective_id='o',now=n)
    x.process(event={'event_id':'e1','event_name':'work.created','work_id':'w1'},objective_id='o',now=n)
    with pytest.raises(ValueError,match='duplicate_event'): x.process(event={'event_id':'e1','event_name':'work.created','work_id':'w1'},objective_id='o',now=n)
    x.process(event={'event_id':'e2','event_name':'work.completed','work_id':'w1'},objective_id='o',now=n)
    with pytest.raises(ValueError,match='terminal'): x.process(event={'event_id':'e3','event_name':'work.started','work_id':'w1'},objective_id='o',now=n)

def test_validation_and_fingerprint():
    x=loop(); n=datetime(2026,1,1,tzinfo=timezone.utc)
    with pytest.raises(ValueError,match='unknown_event'): x.process(event={'event_id':'e1','event_name':'work.nope','work_id':'w1'},objective_id='o',now=n)
    with pytest.raises(ValueError,match='invalid_priority'): x.process(event={'event_id':'e2','event_name':'work.created','work_id':'w1','priority':'urgent'},objective_id='o',now=n)
    with pytest.raises(ValueError,match='invalid_sla'): x.process(event={'event_id':'e3','event_name':'work.created','work_id':'w2','sla_hours':0},objective_id='o',now=n)
    r=x.process(event={'event_id':'e4','event_name':'work.created','work_id':'w3'},objective_id='o',now=n)
    assert r.snapshot.fingerprint()

@pytest.mark.asyncio
async def test_governed_execution_delegates():
    x=loop(); r=await x.handle_event(event={'event_id':'e1','event_name':'work.created','work_id':'w1'},objective_id='o')
    assert r.execution is not None
