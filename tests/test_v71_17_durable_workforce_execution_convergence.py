import asyncio
from types import SimpleNamespace

from services.v71_17_durable_workforce_execution_convergence import DurableWorkforceExecutionConvergence

class FakeRuntime:
    def __init__(self): self.calls=[]

class FakeDB:
    pass

def test_begin_is_deterministic_and_non_executing():
    b = DurableWorkforceExecutionConvergence(production_runtime=object())
    a = asyncio.run(b.begin(run_id='r1', workspace_id='ws'))
    c = asyncio.run(b.begin(run_id='r1', workspace_id='ws'))
    assert a.handoff_id == c.handoff_id
    assert a.correlation_id == 'workforce:ws:r1'
    assert a.status == 'pending'
    assert b.status()['parallel_executor'] is False
    assert b.status()['provider_calls'] is False

def test_claim_is_single_process_fenced():
    b = DurableWorkforceExecutionConvergence(production_runtime=object())
    a = asyncio.run(b.begin(run_id='r1', workspace_id='ws'))
    x = asyncio.run(b.claim(a))
    y = asyncio.run(b.claim(x))
    assert x.status == 'dispatching'
    assert y.status == 'dispatching'

def test_finalize_collects_no_fake_action_evidence_without_db():
    b = DurableWorkforceExecutionConvergence(production_runtime=object())
    a = asyncio.run(b.begin(run_id='r1', workspace_id='ws'))
    x = asyncio.run(b.claim(a))
    z = asyncio.run(b.finalize(x, status='accepted', result_summary={'ok': True}))
    assert z.status == 'accepted'
    assert z.result_summary['durable_action_evidence'] == 'not_configured'

def test_status_reuses_existing_execution_plane():
    b = DurableWorkforceExecutionConvergence(production_runtime=object())
    status = b.status()
    assert status['action_kernel_reused'] is True
    assert status['durable_outbox_reused'] is True
    assert 'V71.10 lease' in status['canonical_path']
