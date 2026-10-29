import json
from pathlib import Path
import pytest
from services.intelligence_decision_engine import IntelligenceDecisionEngine, IntelligenceDecisionPolicy
from services.intelligence_decision_replay import IntelligenceDecisionReplayRepository
from services.intelligence_evidence_graph import IntelligenceEvidenceGraph
from services.intelligence_governance_replay import IntelligenceGovernanceReplayRepository
from services.intelligence_action_intent_replay import IntelligenceActionIntentReplayRepository
from services.intelligence_policy_governance import IntelligencePolicy, RiskClass, GovernanceDisposition


def _setup(tmp_path):
    g=IntelligenceEvidenceGraph()
    s=g.add_node(node_type='signal',workspace_id='ws-1',payload={'v':1},provenance={'source':'test'},node_id='s')
    i=g.add_node(node_type='interpretation',workspace_id='ws-1',payload={'meaning':'replenish'},provenance={'signal_node_id':'s'},node_id='i')
    g.link('s','i')
    p=IntelligenceDecisionPolicy(policy_id='decision-policy',workspace_id='ws-1')
    result=IntelligenceDecisionEngine().construct(graph=g,interpretation_node_id=i.node_id,policy=p,decision={'decision':'replenish'},recommendation={'action_type':'supply_chain.replenish','risk_class':'LOW','confidence':.95,'uncertainty':0.0,'evidence_refs':['s'],'autonomy_level':'L2'})
    d=IntelligenceDecisionReplayRepository(Path(tmp_path)/'d.sqlite'); ds=d.capture(graph=g,result=result)
    gp=IntelligencePolicy(policy_id='gov-policy',workspace_id='ws-1',allowed_action_types=frozenset({'supply_chain.replenish'}))
    gov=IntelligenceGovernanceReplayRepository(Path(tmp_path)/'g.sqlite'); gs=gov.capture(decision_replay=d,decision_replay_id=ds.replay_id,policy=gp)
    evaluation, action = __import__('services.intelligence_policy_governance',fromlist=['IntelligencePolicyGovernor']).IntelligencePolicyGovernor().govern_to_action_intent(graph=g,recommendation_node_id=gs.evaluation['recommendation_node_id'],policy=gp,idempotency_key='idem-1')
    assert evaluation.disposition is GovernanceDisposition.APPROVED
    assert action is not None
    return g,d,gov,gs,action


def test_capture_binds_action_to_historical_governance(tmp_path):
    g,d,gov,gs,action=_setup(tmp_path); repo=IntelligenceActionIntentReplayRepository(Path(tmp_path)/'a.sqlite')
    snap=repo.capture(graph=g,action_intent_node_id=action.node_id,governance_replay=gov,governance_replay_id=gs.replay_id,decision_replay=d)
    assert snap.governance_fingerprint==gs.governance_fingerprint
    assert snap.admission_fingerprint==snap.action_intent_fingerprint


def test_restart_replay_proves_exact_action(tmp_path):
    g,d,gov,gs,action=_setup(tmp_path); path=Path(tmp_path)/'a.sqlite'; repo=IntelligenceActionIntentReplayRepository(path)
    snap=repo.capture(graph=g,action_intent_node_id=action.node_id,governance_replay=gov,governance_replay_id=gs.replay_id,decision_replay=d); repo.close()
    reopened=IntelligenceActionIntentReplayRepository(path); out=reopened.replay(snap.replay_id,governance_replay=gov,decision_replay=d,graph=g)
    assert out.action_intent_fingerprint==snap.action_intent_fingerprint; reopened.close()


def test_replay_rejects_tampered_action(tmp_path):
    g,d,gov,gs,action=_setup(tmp_path); path=Path(tmp_path)/'a.sqlite'; repo=IntelligenceActionIntentReplayRepository(path)
    snap=repo.capture(graph=g,action_intent_node_id=action.node_id,governance_replay=gov,governance_replay_id=gs.replay_id,decision_replay=d)
    value=json.loads(repo._conn.execute('SELECT action_intent_json FROM intelligence_action_intent_replays WHERE replay_id=?',(snap.replay_id,)).fetchone()[0]); value['payload']['action_type']='security.bad'
    repo._conn.execute('UPDATE intelligence_action_intent_replays SET action_intent_json=? WHERE replay_id=?',(json.dumps(value,sort_keys=True),snap.replay_id)); repo._conn.commit()
    with pytest.raises(ValueError,match='ActionIntent fingerprint mismatch|historical ActionIntent'):
        repo.replay(snap.replay_id,governance_replay=gov,decision_replay=d)


def test_replay_rejects_current_graph_substitution(tmp_path):
    g,d,gov,gs,action=_setup(tmp_path); repo=IntelligenceActionIntentReplayRepository(Path(tmp_path)/'a.sqlite')
    snap=repo.capture(graph=g,action_intent_node_id=action.node_id,governance_replay=gov,governance_replay_id=gs.replay_id,decision_replay=d)
    replacement=IntelligenceEvidenceGraph()
    s=replacement.add_node(node_type='signal',workspace_id='ws-1',payload={'v':1},provenance={'source':'test'},node_id='s')
    i=replacement.add_node(node_type='interpretation',workspace_id='ws-1',payload={'meaning':'replenish'},provenance={'signal_node_id':'s'},node_id='i'); replacement.link('s','i')
    de=replacement.add_node(node_type='decision',workspace_id='ws-1',payload={'decision':'replenish'},provenance={'interpretation_node_id':'i'},node_id='d'); replacement.link('i','d')
    r=replacement.add_node(node_type='recommendation',workspace_id='ws-1',payload={'action_type':'supply_chain.replenish','risk_class':'LOW','confidence':.95,'uncertainty':0.0,'evidence_refs':['s'],'autonomy_level':'L2'},provenance={'decision_node_id':'d'},node_id='r'); replacement.link('d','r')
    replacement.add_action_intent(workspace_id='ws-1',action_intent={'action_type':'security.other','idempotency_key':'idem-1','risk_class':'LOW','confidence':.95,'uncertainty':0.0,'governance':{'policy_id':'gov-policy','disposition':'APPROVED'}},recommendation_node_id='r',provenance={'governance_policy_id':'gov-policy','governance_disposition':'APPROVED'})
    # Different node id means historical identity is absent.
    with pytest.raises(ValueError): repo.replay(snap.replay_id,governance_replay=gov,decision_replay=d,graph=replacement)


def test_replay_rejects_policy_substitution(tmp_path):
    g,d,gov,gs,action=_setup(tmp_path); repo=IntelligenceActionIntentReplayRepository(Path(tmp_path)/'a.sqlite')
    snap=repo.capture(graph=g,action_intent_node_id=action.node_id,governance_replay=gov,governance_replay_id=gs.replay_id,decision_replay=d)
    changed=IntelligenceGovernanceReplayRepository(Path(tmp_path)/'changed.sqlite')
    # The original governance repository remains the authoritative historical source;
    # a missing governance replay must not be substituted.
    with pytest.raises(KeyError): repo.replay(snap.replay_id,governance_replay=changed,decision_replay=d)


def test_workspace_isolation(tmp_path):
    g,d,gov,gs,action=_setup(tmp_path); repo=IntelligenceActionIntentReplayRepository(Path(tmp_path)/'a.sqlite')
    snap=repo.capture(graph=g,action_intent_node_id=action.node_id,governance_replay=gov,governance_replay_id=gs.replay_id,decision_replay=d)
    with pytest.raises(PermissionError,match='workspace mismatch'): repo.replay(snap.replay_id,governance_replay=gov,decision_replay=d,workspace_id='other')


def test_replay_is_not_authorization_or_execution_authority(tmp_path):
    g,d,gov,gs,action=_setup(tmp_path); repo=IntelligenceActionIntentReplayRepository(Path(tmp_path)/'a.sqlite')
    snap=repo.capture(graph=g,action_intent_node_id=action.node_id,governance_replay=gov,governance_replay_id=gs.replay_id,decision_replay=d)
    out=repo.replay(snap.replay_id,governance_replay=gov,decision_replay=d)
    assert 'authorization' not in out.action_intent and 'execution_authority' not in out.action_intent
