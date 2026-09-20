from services.v72_7_decision_intelligence_outcome_attribution import BusinessDecisionIntelligence, AttributionState


def test_direct_recommendation_outcome_link_is_observed_not_causal():
    rec = {"recommendationId":"r1", "fingerprint":"rfp", "outcomeLinks":["o1"]}
    out = {"observationId":"o1", "businessSuccess":True}
    result = BusinessDecisionIntelligence("w1", [rec], [], [out]).build()
    attr = result["recommendations"][0]["outcomeAttributions"][0]
    assert attr["state"] == AttributionState.OBSERVED
    assert attr["causalClaim"] is False
    assert result["authority"]["actionAuthorityGranted"] is False


def test_decision_link_is_recorded_without_granting_authority():
    rec = {"recommendationId":"r1", "outcomeLinks":[]}
    decision = {"decisionId":"d1", "recommendationId":"r1", "state":"APPROVED", "source":"governance"}
    result = BusinessDecisionIntelligence("w1", [rec], [decision], []).build()
    row = result["recommendations"][0]
    assert row["decisions"][0]["decisionId"] == "d1"
    assert row["decisions"][0]["actionAuthorityGranted"] is False


def test_decision_linked_outcome_is_associated_not_causal():
    rec = {"recommendationId":"r1"}
    decision = {"decisionId":"d1", "recommendationId":"r1", "state":"APPROVED"}
    outcome = {"observationId":"o1", "decisionId":"d1", "businessSuccess":True}
    result = BusinessDecisionIntelligence("w1", [rec], [decision], [outcome]).build()
    attr = result["recommendations"][0]["outcomeAttributions"][0]
    assert attr["state"] == AttributionState.ASSOCIATED
    assert attr["causalClaim"] is False


def test_missing_chain_is_insufficient_evidence():
    rec = {"recommendationId":"r1"}
    result = BusinessDecisionIntelligence("w1", [rec], [], []).build()
    attr = result["recommendations"][0]["outcomeAttributions"][0]
    assert attr["state"] == AttributionState.INSUFFICIENT_EVIDENCE


def test_tenant_isolation_by_instance_and_deterministic_order():
    recs = [{"recommendationId":"z"}, {"recommendationId":"a"}]
    result = BusinessDecisionIntelligence("tenant-a", recs, [], []).build()
    assert result["workspaceId"] == "tenant-a"
    assert [x["recommendationId"] for x in result["recommendations"]] == ["a", "z"]
    assert BusinessDecisionIntelligence("tenant-b", recs, [], []).build()["workspaceId"] == "tenant-b"
