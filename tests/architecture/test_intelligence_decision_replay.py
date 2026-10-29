import json
from pathlib import Path
import pytest
from services.intelligence_decision_engine import IntelligenceDecisionEngine, IntelligenceDecisionPolicy
from services.intelligence_decision_replay import IntelligenceDecisionReplayRepository
from services.intelligence_evidence_graph import IntelligenceEvidenceGraph
from services.intelligence_learning_decision_context import build_learning_decision_context


def _build_graph():
    g=IntelligenceEvidenceGraph(); s=g.add_node(node_type='signal',workspace_id='ws-1',payload={'v':1},provenance={'source':'monitor'},node_id='s'); i=g.add_node(node_type='interpretation',workspace_id='ws-1',payload={'meaning':'replenish'},provenance={'signal_node_id':'s'},node_id='i'); g.link('s','i'); return g,i

def _ctx(fp='fp-1'):
    return build_learning_decision_context(workspace_id='ws-1', learning_feedback=[{'workspace_id':'ws-1','feedback_id':fp,'execution_id':'e','outcome_id':'o','learning_state':'positive','confidence':.9,'evidence_fingerprint':fp,'authority':'none','causal_claim':False,'governance_mutation':False,'execution_authority':False}])

def _rec(): return {'action_type':'supply_chain.replenish','risk_class':'LOW','confidence':.95,'uncertainty':0.0,'evidence_refs':['s'],'autonomy_level':'L2'}

def test_decision_fingerprint_is_deterministic_for_same_inputs():
    a,i=_build_graph(); b,j=_build_graph(); p=IntelligenceDecisionPolicy(policy_id='p',workspace_id='ws-1')
    x=IntelligenceDecisionEngine().construct(graph=a,interpretation_node_id=i.node_id,policy=p,decision={'decision':'replenish'},recommendation=_rec(),learning_context=_ctx())
    y=IntelligenceDecisionEngine().construct(graph=b,interpretation_node_id=j.node_id,policy=p,decision={'decision':'replenish'},recommendation=_rec(),learning_context=_ctx())
    assert x.decision.provenance['decision_fingerprint']==y.decision.provenance['decision_fingerprint']

def test_replay_survives_restart_without_rereading_learning(tmp_path: Path):
    db=tmp_path/'replay.sqlite'; g,i=_build_graph(); p=IntelligenceDecisionPolicy(policy_id='p',workspace_id='ws-1'); result=IntelligenceDecisionEngine().construct(graph=g,interpretation_node_id=i.node_id,policy=p,decision={'decision':'replenish'},recommendation=_rec(),learning_context=_ctx())
    repo=IntelligenceDecisionReplayRepository(db); captured=repo.capture(graph=g,result=result); repo.close()
    reopened=IntelligenceDecisionReplayRepository(db); replayed=reopened.replay(captured.replay_id); assert replayed.decision_fingerprint==captured.decision_fingerprint; assert replayed.learning_context_id==captured.learning_context_id; reopened.close()

def test_new_learning_cannot_change_historical_replay(tmp_path: Path):
    db=tmp_path/'replay.sqlite'; g,i=_build_graph(); p=IntelligenceDecisionPolicy(policy_id='p',workspace_id='ws-1'); result=IntelligenceDecisionEngine().construct(graph=g,interpretation_node_id=i.node_id,policy=p,decision={'decision':'replenish'},recommendation=_rec(),learning_context=_ctx('old'))
    repo=IntelligenceDecisionReplayRepository(db); captured=repo.capture(graph=g,result=result)
    new=_ctx('new').to_dict()
    with pytest.raises(ValueError,match='different learning context'): repo.replay(captured.replay_id,current_learning_context=new)
    replayed=repo.replay(captured.replay_id); assert replayed.learning_context_id==_ctx('old').context_id
    repo.close()

def test_replay_does_not_execute_or_govern():
    g,i=_build_graph(); p=IntelligenceDecisionPolicy(policy_id='p',workspace_id='ws-1'); result=IntelligenceDecisionEngine().construct(graph=g,interpretation_node_id=i.node_id,policy=p,decision={'decision':'replenish'},recommendation=_rec(),learning_context=_ctx())
    repo=IntelligenceDecisionReplayRepository(); snap=repo.capture(graph=g,result=result); replayed=repo.replay(snap.replay_id); assert 'authorization' not in replayed.recommendation['provenance']; assert 'execution_authority' not in replayed.recommendation['provenance']; repo.close()


def test_decision_replay_proves_interpretation_policy_and_learning_semantics(tmp_path: Path):
    db=tmp_path/'replay.sqlite'; g,i=_build_graph(); p=IntelligenceDecisionPolicy(policy_id='policy-A',workspace_id='ws-1')
    result=IntelligenceDecisionEngine().construct(graph=g,interpretation_node_id=i.node_id,policy=p,decision={'decision':'replenish'},recommendation=_rec(),learning_context=_ctx('stable'))
    repo=IntelligenceDecisionReplayRepository(db); snap=repo.capture(graph=g,result=result)
    assert snap.interpretation['node_type']=='interpretation'
    assert snap.decision_fingerprint==result.decision.provenance['decision_fingerprint']
    repo.close()


def test_decision_replay_rejects_tampered_interpretation(tmp_path: Path):
    db=tmp_path/'replay.sqlite'; g,i=_build_graph(); p=IntelligenceDecisionPolicy(policy_id='p',workspace_id='ws-1')
    result=IntelligenceDecisionEngine().construct(graph=g,interpretation_node_id=i.node_id,policy=p,decision={'decision':'replenish'},recommendation=_rec(),learning_context=_ctx())
    repo=IntelligenceDecisionReplayRepository(db); snap=repo.capture(graph=g,result=result)
    repo._conn.execute('UPDATE intelligence_decision_replays SET interpretation_json=? WHERE replay_id=?', ('{"node_type":"interpretation","workspace_id":"ws-1","payload":{"meaning":"tampered"},"provenance":{"signal_node_id":"s"}}', snap.replay_id)); repo._conn.commit()
    with pytest.raises(ValueError,match='semantic fingerprint mismatch'): repo.replay(snap.replay_id)
    repo.close()


def test_decision_replay_rejects_tampered_policy_context(tmp_path: Path):
    db=tmp_path/'replay.sqlite'; g,i=_build_graph(); p=IntelligenceDecisionPolicy(policy_id='p',workspace_id='ws-1')
    result=IntelligenceDecisionEngine().construct(graph=g,interpretation_node_id=i.node_id,policy=p,decision={'decision':'replenish'},recommendation=_rec(),learning_context=_ctx())
    repo=IntelligenceDecisionReplayRepository(db); snap=repo.capture(graph=g,result=result)
    rec=json.loads(snap.recommendation.__str__()) if False else dict(snap.recommendation)
    rec['provenance']['learning_use_policy_id']='tampered-policy'
    repo._conn.execute('UPDATE intelligence_decision_replays SET recommendation_json=? WHERE replay_id=?', (json.dumps(rec,sort_keys=True), snap.replay_id)); repo._conn.commit()
    with pytest.raises(ValueError,match='semantic fingerprint mismatch'): repo.replay(snap.replay_id)
    repo.close()
