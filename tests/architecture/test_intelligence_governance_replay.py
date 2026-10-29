import json
from pathlib import Path
import pytest
from services.intelligence_decision_engine import IntelligenceDecisionEngine, IntelligenceDecisionPolicy
from services.intelligence_decision_replay import IntelligenceDecisionReplayRepository
from services.intelligence_evidence_graph import IntelligenceEvidenceGraph
from services.intelligence_governance_replay import IntelligenceGovernanceReplayRepository
from services.intelligence_policy_governance import GovernanceDisposition, IntelligencePolicy, RiskClass


def _graph():
    g=IntelligenceEvidenceGraph(); s=g.add_node(node_type='signal',workspace_id='ws-1',payload={'v':1},provenance={'source':'test'},node_id='s')
    i=g.add_node(node_type='interpretation',workspace_id='ws-1',payload={'meaning':'replenish'},provenance={'signal_node_id':'s'},node_id='i'); g.link('s','i')
    return g,i

def _decision(tmp_path):
    g,i=_graph(); p=IntelligenceDecisionPolicy(policy_id='decision-policy',workspace_id='ws-1')
    rec={'action_type':'supply_chain.replenish','risk_class':'HIGH','confidence':.95,'uncertainty':0.0,'evidence_refs':['s'],'autonomy_level':'L2'}
    result=IntelligenceDecisionEngine().construct(graph=g,interpretation_node_id=i.node_id,policy=p,decision={'decision':'replenish'},recommendation=rec)
    d=IntelligenceDecisionReplayRepository(Path(tmp_path)/'decision.sqlite'); snap=d.capture(graph=g,result=result)
    return d,snap

def _policy():
    return IntelligencePolicy(policy_id='gov-policy',workspace_id='ws-1',min_confidence_by_risk={RiskClass.LOW:.6,RiskClass.MEDIUM:.75,RiskClass.HIGH:.9,RiskClass.CRITICAL:.95},approval_required_by_risk={RiskClass.LOW:False,RiskClass.MEDIUM:False,RiskClass.HIGH:True,RiskClass.CRITICAL:True})

def test_capture_freezes_exact_policy_and_disposition(tmp_path: Path):
    d,snap=_decision(tmp_path); r=IntelligenceGovernanceReplayRepository(Path(tmp_path)/'gov.sqlite'); captured=r.capture(decision_replay=d,decision_replay_id=snap.replay_id,policy=_policy())
    assert captured.evaluation['disposition']==GovernanceDisposition.REQUIRES_APPROVAL.value
    assert captured.evaluation['requires_human_approval'] is True
    assert captured.policy['policy_id']=='gov-policy'; r.close(); d.close()

def test_replay_uses_frozen_policy_after_live_policy_changes(tmp_path: Path):
    d,snap=_decision(tmp_path); r=IntelligenceGovernanceReplayRepository(Path(tmp_path)/'gov.sqlite'); old=_policy(); captured=r.capture(decision_replay=d,decision_replay_id=snap.replay_id,policy=old)
    replayed=r.replay(captured.replay_id,decision_replay=d)
    assert replayed.evaluation==captured.evaluation
    changed=IntelligencePolicy(policy_id='gov-policy',workspace_id='ws-1',approval_required_by_risk={RiskClass.LOW:False,RiskClass.MEDIUM:False,RiskClass.HIGH:False,RiskClass.CRITICAL:False})
    with pytest.raises(ValueError,match='different policy'): r.replay(captured.replay_id,decision_replay=d,current_policy=changed)
    r.close(); d.close()

def test_replay_rejects_tampered_policy(tmp_path: Path):
    d,snap=_decision(tmp_path); r=IntelligenceGovernanceReplayRepository(Path(tmp_path)/'gov.sqlite'); captured=r.capture(decision_replay=d,decision_replay_id=snap.replay_id,policy=_policy())
    policy=json.loads(r._conn.execute('SELECT policy_json FROM intelligence_governance_replays WHERE replay_id=?',(captured.replay_id,)).fetchone()[0]); policy['max_uncertainty']=1.0
    r._conn.execute('UPDATE intelligence_governance_replays SET policy_json=? WHERE replay_id=?',(json.dumps(policy,sort_keys=True),captured.replay_id)); r._conn.commit()
    with pytest.raises(ValueError,match='policy fingerprint mismatch'): r.replay(captured.replay_id,decision_replay=d)
    r.close(); d.close()

def test_replay_rejects_tampered_evaluation(tmp_path: Path):
    d,snap=_decision(tmp_path); r=IntelligenceGovernanceReplayRepository(Path(tmp_path)/'gov.sqlite'); captured=r.capture(decision_replay=d,decision_replay_id=snap.replay_id,policy=_policy())
    evaluation=dict(captured.evaluation); evaluation['disposition']=GovernanceDisposition.APPROVED.value
    r._conn.execute('UPDATE intelligence_governance_replays SET evaluation_json=? WHERE replay_id=?',(json.dumps(evaluation,sort_keys=True),captured.replay_id)); r._conn.commit()
    with pytest.raises(ValueError,match='evaluation fingerprint mismatch'): r.replay(captured.replay_id,decision_replay=d)
    r.close(); d.close()

def test_workspace_isolation(tmp_path: Path):
    d,snap=_decision(tmp_path); r=IntelligenceGovernanceReplayRepository(Path(tmp_path)/'gov.sqlite'); captured=r.capture(decision_replay=d,decision_replay_id=snap.replay_id,policy=_policy())
    with pytest.raises(PermissionError,match='workspace mismatch'): r.replay(captured.replay_id,decision_replay=d,workspace_id='other')
    r.close(); d.close()

def test_governance_replay_is_not_execution_authority(tmp_path: Path):
    d,snap=_decision(tmp_path); r=IntelligenceGovernanceReplayRepository(Path(tmp_path)/'gov.sqlite'); captured=r.capture(decision_replay=d,decision_replay_id=snap.replay_id,policy=_policy()); replayed=r.replay(captured.replay_id,decision_replay=d)
    assert replayed.evaluation['governed'] is True
    assert 'authorization' not in replayed.evaluation
    assert 'execution_authority' not in replayed.evaluation
    r.close(); d.close()
