from services.v72_2_command_center_read_model import TruthValue
from services.v72_5_business_intelligence_decision_surface import (
    BusinessIntelligenceDecisionSurface, IntelligenceState,
)


def tv(section, items):
    return TruthValue(section, "LIVE", "OBSERVED", "test", "now", "HIGH",
                      ({"source": section},), {"items": items, "count": len(items)})


def test_recommendations_are_deterministic_and_no_authority():
    truth = {
        "objectives": tv("objectives", [{"objectiveId": "o1", "state": "active"}]),
        "risks": tv("risks", [{"workId": "w1", "title": "SLA", "priority": "critical", "slaBreached": True}]),
        "opportunities": tv("opportunities", [{"opportunityId": "d1", "stage": "NEGOTIATION", "amount": 500}]),
        "workforce": tv("workforce", []), "outcomes": tv("outcomes", []),
    }
    a = BusinessIntelligenceDecisionSurface("ws-1", truth).build()
    b = BusinessIntelligenceDecisionSurface("ws-1", truth).build()
    assert [x["fingerprint"] for x in a["recommendations"]] == [x["fingerprint"] for x in b["recommendations"]]
    assert a["authority"]["actionAuthorityGranted"] is False
    assert all(x["actionAuthorityGranted"] is False for x in a["recommendations"])


def test_missing_objective_exposes_capability_unavailable():
    result = BusinessIntelligenceDecisionSurface("ws-1", {"objectives": tv("objectives", [])}).build()
    rec = next(x for x in result["recommendations"] if x["state"] == IntelligenceState.CAPABILITY_UNAVAILABLE)
    assert rec["capabilityResolution"] == "CAPABILITY_UNAVAILABLE"
    assert rec["actionAuthorityGranted"] is False


def test_uncertain_workforce_run_requires_governance_review():
    truth = {"objectives": tv("objectives", [{"objectiveId": "o1", "state": "active"}]),
             "workforce": tv("workforce", [{"runId": "r1", "status": "PAUSED", "disposition": "RECONCILIATION_REQUIRED"}])}
    result = BusinessIntelligenceDecisionSurface("ws-1", truth).build()
    rec = next(x for x in result["recommendations"] if x["domain"] == "workforce")
    assert rec["capabilityResolution"] == "GOVERNANCE_REVIEW_REQUIRED"
    assert rec["risk"] == "HIGH"


def test_negative_outcome_is_advisory_and_evidence_linked():
    truth = {"objectives": tv("objectives", [{"objectiveId": "o1", "state": "active"}]),
             "outcomes": tv("outcomes", [{"observationId": "obs1", "businessSuccess": False}])}
    result = BusinessIntelligenceDecisionSurface("ws-1", truth).build()
    rec = next(x for x in result["recommendations"] if x["domain"] == "outcomes")
    assert rec["state"] == "ADVISORY"
    assert rec["capabilityResolution"] == "ADVISORY_ONLY"
    assert rec["evidence"]


def test_tenant_identity_is_embedded_and_fingerprinted():
    truth = {"objectives": tv("objectives", [])}
    a = BusinessIntelligenceDecisionSurface("ws-a", truth).build()["recommendations"][0]
    b = BusinessIntelligenceDecisionSurface("ws-b", truth).build()["recommendations"][0]
    assert a["workspaceId"] == "ws-a"
    assert b["workspaceId"] == "ws-b"
    assert a["fingerprint"] != b["fingerprint"]
