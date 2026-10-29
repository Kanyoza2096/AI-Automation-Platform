import json
from pathlib import Path
import pytest
from core.request_context import RequestContext
from services.intelligence_decision_engine import IntelligenceDecisionEngine, IntelligenceDecisionPolicy
from services.intelligence_decision_replay import IntelligenceDecisionReplayRepository
from services.intelligence_evidence_graph import IntelligenceEvidenceGraph
from services.intelligence_governance_replay import IntelligenceGovernanceReplayRepository
from services.intelligence_action_intent_replay import IntelligenceActionIntentReplayRepository
from services.intelligence_authorization_replay import IntelligenceCanonicalAuthorizationReplayRepository
from services.intelligence_policy_governance import IntelligencePolicy, GovernanceDisposition


def _setup(tmp_path):
    g=IntelligenceEvidenceGraph()
    s=g.add_node(node_type='signal',workspace_id='ws-1',payload={'v':1},provenance={'source':'test'},node_id='s')
    i=g.add_node(node_type='interpretation',workspace_id='ws-1',payload={'meaning':'replenish'},provenance={'signal_node_id':'s'},node_id='i'); g.link('s','i')
    p=IntelligenceDecisionPolicy(policy_id='decision-policy',workspace_id='ws-1')
    result=IntelligenceDecisionEngine().construct(graph=g,interpretation_node_id=i.node_id,policy=p,decision={'decision':'replenish'},recommendation={'action_type':'supply_chain.replenish','risk_class':'LOW','confidence':.95,'uncertainty':0.0,'evidence_refs':['s'],'autonomy_level':'L2'})
    d=IntelligenceDecisionReplayRepository(Path(tmp_path)/'d.sqlite'); ds=d.capture(graph=g,result=result)
    gp=IntelligencePolicy(policy_id='gov-policy',workspace_id='ws-1',allowed_action_types=frozenset({'supply_chain.replenish'}))
    gov=IntelligenceGovernanceReplayRepository(Path(tmp_path)/'g.sqlite'); gs=gov.capture(decision_replay=d,decision_replay_id=ds.replay_id,policy=gp)
    from services.intelligence_policy_governance import IntelligencePolicyGovernor
    evaluation, action=IntelligencePolicyGovernor().govern_to_action_intent(graph=g,recommendation_node_id=gs.evaluation['recommendation_node_id'],policy=gp,idempotency_key='idem-1')
    assert evaluation.disposition is GovernanceDisposition.APPROVED and action is not None
    ai=IntelligenceActionIntentReplayRepository(Path(tmp_path)/'a.sqlite')
    ais=ai.capture(graph=g,action_intent_node_id=action.node_id,governance_replay=gov,governance_replay_id=gs.replay_id,decision_replay=d)
    ctx=RequestContext(principal_id='u1',workspace_id='ws-1',role='operator',permissions=('write',),correlation_id='c1')
    return g,d,gov,ai,ais,ctx


def test_capture_binds_canonical_authorization(tmp_path):
    *_, ai, ais, ctx = _setup(tmp_path)
    repo=IntelligenceCanonicalAuthorizationReplayRepository(Path(tmp_path)/'z.sqlite')
    snap=repo.capture_from_snapshot(action_intent_snapshot=ais,request_context=ctx)
    assert snap.action_intent_fingerprint==ais.action_intent_fingerprint
    assert snap.pipeline['allowed'] is True
    assert snap.authorization_fingerprint


def test_restart_replay_does_not_reauthorize_with_current_context(tmp_path):
    *_, ai, ais, ctx = _setup(tmp_path)
    path=Path(tmp_path)/'z.sqlite'; repo=IntelligenceCanonicalAuthorizationReplayRepository(path)
    snap=repo.capture_from_snapshot(action_intent_snapshot=ais,request_context=ctx); repo.close()
    reopened=IntelligenceCanonicalAuthorizationReplayRepository(path)
    current= RequestContext(principal_id='different',workspace_id='ws-1',role='viewer',permissions=(),correlation_id='new')
    out=reopened.replay(snap.replay_id,action_intent_replay=ai,action_intent_snapshot=ais,workspace_id='ws-1')
    assert out.authorization_fingerprint==snap.authorization_fingerprint
    assert out.authorization_context['role']=='operator'
    assert current.role=='viewer'


def test_replay_rejects_action_substitution(tmp_path):
    *_, ai, ais, ctx = _setup(tmp_path)
    repo=IntelligenceCanonicalAuthorizationReplayRepository(Path(tmp_path)/'z.sqlite')
    snap=repo.capture_from_snapshot(action_intent_snapshot=ais,request_context=ctx)
    forged=type(ais)(**{**ais.__dict__, 'action_intent_fingerprint':'different'})
    with pytest.raises(ValueError,match='substitution'):
        repo.replay(snap.replay_id,action_intent_replay=ai,action_intent_snapshot=forged)


def test_replay_rejects_tampered_authorization_record(tmp_path):
    *_, ai, ais, ctx = _setup(tmp_path)
    repo=IntelligenceCanonicalAuthorizationReplayRepository(Path(tmp_path)/'z.sqlite')
    snap=repo.capture_from_snapshot(action_intent_snapshot=ais,request_context=ctx)
    row=repo._conn.execute('SELECT pipeline_json FROM intelligence_authorization_replays WHERE replay_id=?',(snap.replay_id,)).fetchone()
    value=json.loads(row[0]); value['allowed']=False
    repo._conn.execute('UPDATE intelligence_authorization_replays SET pipeline_json=? WHERE replay_id=?',(json.dumps(value,sort_keys=True),snap.replay_id)); repo._conn.commit()
    with pytest.raises(ValueError,match='fingerprint'):
        repo.replay(snap.replay_id,action_intent_replay=ai,action_intent_snapshot=ais)


def test_workspace_isolation(tmp_path):
    *_, ai, ais, ctx = _setup(tmp_path)
    repo=IntelligenceCanonicalAuthorizationReplayRepository(Path(tmp_path)/'z.sqlite')
    snap=repo.capture_from_snapshot(action_intent_snapshot=ais,request_context=ctx)
    with pytest.raises(PermissionError,match='workspace mismatch'):
        repo.replay(snap.replay_id,action_intent_replay=ai,action_intent_snapshot=ais,workspace_id='other')


def test_replay_is_evidence_not_authority(tmp_path):
    *_, ai, ais, ctx = _setup(tmp_path)
    repo=IntelligenceCanonicalAuthorizationReplayRepository(Path(tmp_path)/'z.sqlite')
    snap=repo.capture_from_snapshot(action_intent_snapshot=ais,request_context=ctx)
    out=repo.replay(snap.replay_id,action_intent_replay=ai,action_intent_snapshot=ais)
    assert 'execution_authority' not in out.pipeline
    assert 'authorization_granted' not in out.pipeline
