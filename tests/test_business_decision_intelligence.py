from models.business_decision_intelligence import DecisionDisposition
from services.business_decision_intelligence import BusinessDecisionIntelligenceProjector


def candidate(ws="w1", cid="c1"):
    return {"schema_version":"1.0","candidate_id":cid,"understanding_id":"u1","workspace_id":ws,"candidate_action":"crm_schedule_followup","status":"proposed","intent":"purchase_interest","intent_confidence":0.9,"governance_score":0.9,"governance_status":"decision_eligible","constraints":["must_be_governed"],"authority":"none","reason":"bounded"}


def evaluation(ws="w1", eid="e1", cid="c1", disposition="ALLOW"):
    return {"schema_version":"1.0","evaluation_id":eid,"candidate_id":cid,"understanding_id":"u1","workspace_id":ws,"action":"crm_schedule_followup","intent":"purchase_interest","risk_level":"medium","policy_version":"v65.7.1","disposition":disposition,"reason":"policy result","approval_required":disposition=="APPROVAL_REQUIRED","review_required":disposition=="REVIEW","governance_status":"decision_eligible","candidate_status":"proposed","constraints":["must_be_governed"],"authority":"none"}


def test_projects_policy_evaluation_into_canonical_decision():
    result = BusinessDecisionIntelligenceProjector().project(workspace_id="w1", candidates=[candidate()], evaluations=[evaluation()])
    assert len(result.decisions) == 1
    assert result.decisions[0].disposition is DecisionDisposition.RECOMMEND
    assert result.decisions[0].action_authority_granted is False
    assert result.decisions[0].candidate_id == "c1"


def test_evaluation_without_candidate_never_becomes_decision():
    result = BusinessDecisionIntelligenceProjector().project(workspace_id="w1", evaluations=[evaluation()])
    assert result.decisions == []
    assert "evaluation_missing_candidate:e1" in result.warnings


def test_workspace_isolation():
    result = BusinessDecisionIntelligenceProjector().project(workspace_id="w1", candidates=[candidate("w2")], evaluations=[evaluation("w2")])
    assert result.decisions == []
    assert result.coverage["evaluations"] == 0


def test_deterministic_decision_identity():
    p = BusinessDecisionIntelligenceProjector()
    a = p.project(workspace_id="w1", candidates=[candidate()], evaluations=[evaluation()])
    b = p.project(workspace_id="w1", candidates=[candidate()], evaluations=[evaluation()])
    assert a.model_dump() == b.model_dump()
    assert a.decisions[0].decision_id.startswith("decision_")


def test_review_and_block_are_preserved():
    p = BusinessDecisionIntelligenceProjector()
    result = p.project(workspace_id="w1", candidates=[candidate(cid="c1"), candidate(cid="c2")], evaluations=[evaluation(eid="e1", cid="c1", disposition="REVIEW"), evaluation(eid="e2", cid="c2", disposition="BLOCK")])
    assert {d.disposition for d in result.decisions} == {DecisionDisposition.REVIEW, DecisionDisposition.BLOCK}
