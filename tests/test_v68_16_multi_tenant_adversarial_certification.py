import pytest

from services.v68_16_multi_tenant_adversarial_certification import (
    build_tenant_journey,
    run_adversarial_certification,
)


def test_two_tenant_journeys_are_certified_and_lineage_is_disjoint():
    report = run_adversarial_certification()
    assert report["status"] == "CERTIFIED"
    assert report["tenants"] == ["tenant_A", "tenant_B"]
    assert report["cross_tenant_lineage_overlap"] == 0
    assert report["adversarial_cases"] == 6
    assert report["external_side_effects"] == 0


def test_tenant_journey_reaches_promoted_advisory_learning():
    journey = build_tenant_journey("tenant_X", execution_id="exec_X")
    assert journey.evaluation["disposition"] == "ALLOW"
    assert journey.attribution["attribution_status"] == "attributed"
    assert journey.governed_learning[0]["promotion_state"] == "promoted"
    assert journey.decision_context["advisory_only"] is True
    assert journey.decision_context["authority"] == "none"


def test_cross_tenant_attribution_never_selects_other_tenant_execution():
    a = build_tenant_journey("tenant_A2", execution_id="exec_A2")
    b = build_tenant_journey("tenant_B2", execution_id="exec_B2")
    result = __import__("services.business_outcome_attribution", fromlist=["attribute_business_outcome"]).attribute_business_outcome(
        a.outcome, [b.execution]
    )
    assert result["attribution_status"] != "attributed"
    assert result["execution_id"] is None
