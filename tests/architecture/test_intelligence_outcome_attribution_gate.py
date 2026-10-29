import pytest
from services.intelligence_outcome_attribution_gate import IntelligenceOutcomeAttributionGate, AttributionState


def _data():
    p = {"action_intent_node_id":"ai-1","admission_fingerprint":"af-1","recommendation_node_id":"rec-1","policy_id":"pol-1"}
    e = {"workspace_id":"ws-1","execution_id":"exe-1","status":"succeeded","provider_object_id":"prov-1","intelligence_provenance":dict(p),"receipt":{"status":"succeeded","provider_object_id":"prov-1"}}
    v = {"workspace_id":"ws-1","verification_id":"ver-1","execution_id":"exe-1","state":"verified_provider" ,"provider_object_id":"prov-1"}
    r = {"workspace_id":"ws-1","reconciliation_id":"recn-1","execution_id":"exe-1","status":"verified_provider_terminal","terminal":True}
    o = {"workspace_id":"ws-1","outcome_id":"out-1","execution_id":"exe-1","reconciliation_id":"recn-1"}
    return p,e,v,r,o


def test_authoritative_chain_is_attributed():
    p,e,v,r,o = _data()
    result = IntelligenceOutcomeAttributionGate().attribute(workspace_id="ws-1", intelligence_provenance=p, execution=e, verification=v, reconciliation=r, outcome=o)
    assert result.state == AttributionState.VERIFIED
    assert result.provider_object_id == "prov-1"
    assert result.to_dict()["causal_claim"] is False
    assert result.to_dict()["action_authority_granted"] is False


def test_missing_verification_fails_closed():
    p,e,v,r,o = _data(); v["state"] = "unknown"
    with pytest.raises(ValueError, match="authoritative verification"):
        IntelligenceOutcomeAttributionGate().attribute(workspace_id="ws-1", intelligence_provenance=p, execution=e, verification=v, reconciliation=r, outcome=o)


def test_nonterminal_reconciliation_fails_closed():
    p,e,v,r,o = _data(); r["terminal"] = False; r["status"] = "in_progress"
    with pytest.raises(ValueError, match="terminal authoritative reconciliation"):
        IntelligenceOutcomeAttributionGate().attribute(workspace_id="ws-1", intelligence_provenance=p, execution=e, verification=v, reconciliation=r, outcome=o)


def test_uncertain_receipt_cannot_become_authoritative_outcome():
    p,e,v,r,o = _data(); e["status"] = "uncertain"; e["receipt"]["status"] = "uncertain"
    with pytest.raises(ValueError, match="uncertain provider receipt"):
        IntelligenceOutcomeAttributionGate().attribute(workspace_id="ws-1", intelligence_provenance=p, execution=e, verification=v, reconciliation=r, outcome=o)


def test_provider_identity_mismatch_is_rejected():
    p,e,v,r,o = _data(); v["provider_object_id"] = "other"
    with pytest.raises(ValueError, match="provider identity"):
        IntelligenceOutcomeAttributionGate().attribute(workspace_id="ws-1", intelligence_provenance=p, execution=e, verification=v, reconciliation=r, outcome=o)


def test_execution_provenance_mismatch_is_rejected():
    p,e,v,r,o = _data(); e["intelligence_provenance"]["admission_fingerprint"] = "forged"
    with pytest.raises(ValueError, match="provenance mismatch"):
        IntelligenceOutcomeAttributionGate().attribute(workspace_id="ws-1", intelligence_provenance=p, execution=e, verification=v, reconciliation=r, outcome=o)


def test_workspace_mismatch_is_rejected():
    p,e,v,r,o = _data(); v["workspace_id"] = "ws-2"
    with pytest.raises(PermissionError, match="workspace mismatch"):
        IntelligenceOutcomeAttributionGate().attribute(workspace_id="ws-1", intelligence_provenance=p, execution=e, verification=v, reconciliation=r, outcome=o)


def test_outcome_cannot_point_to_different_reconciliation():
    p,e,v,r,o = _data(); o["reconciliation_id"] = "other"
    with pytest.raises(ValueError, match="reconciliation mismatch"):
        IntelligenceOutcomeAttributionGate().attribute(workspace_id="ws-1", intelligence_provenance=p, execution=e, verification=v, reconciliation=r, outcome=o)


def test_attribution_fingerprint_is_deterministic():
    p,e,v,r,o = _data(); gate = IntelligenceOutcomeAttributionGate()
    a = gate.attribute(workspace_id="ws-1", intelligence_provenance=p, execution=e, verification=v, reconciliation=r, outcome=o)
    b = gate.attribute(workspace_id="ws-1", intelligence_provenance=p, execution=e, verification=v, reconciliation=r, outcome=o)
    assert a.evidence_fingerprint == b.evidence_fingerprint
