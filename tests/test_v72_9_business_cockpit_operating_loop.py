from services.v72_9_business_cockpit_operating_loop import BusinessCockpitOperatingLoop, LoopState


def rec(**kw):
    x = {"recommendationId": "r1", "title": "Review opportunity", "risk": "MEDIUM",
         "capability": "sales.follow-up-due", "capabilityResolution": "EXECUTABLE_CANDIDATE",
         "evidence": [{"source": "opportunity", "fingerprint": "abc"}]}
    x.update(kw)
    return x


def test_operating_loop_composes_human_work_item_without_authority():
    out = BusinessCockpitOperatingLoop("w1", truth={"objectives": True}, recommendations=[rec()]).build()
    assert out["state"] == LoopState.ATTENTION_REQUIRED
    item = out["workItems"][0]
    assert item["commandIntent"]["authorityGranted"] is False
    assert item["commandIntent"]["executionRequested"] is False
    assert out["authority"]["actionAuthorityGranted"] is False


def test_high_risk_requires_governance():
    out = BusinessCockpitOperatingLoop("w1", recommendations=[rec(risk="HIGH")]).build()
    assert out["workItems"][0]["state"] == LoopState.GOVERNANCE_REQUIRED
    assert out["workItems"][0]["commandIntent"] is None


def test_advisory_and_unavailable_do_not_create_commands():
    rows = [rec(recommendationId="a", capabilityResolution="ADVISORY_ONLY"),
            rec(recommendationId="b", capabilityResolution="CAPABILITY_UNAVAILABLE")]
    out = BusinessCockpitOperatingLoop("w1", recommendations=rows).build()
    assert all(x["commandIntent"] is None for x in out["workItems"])


def test_deterministic_work_item_identity_is_tenant_scoped():
    a = BusinessCockpitOperatingLoop("w1", recommendations=[rec()]).build()["workItems"][0]
    b = BusinessCockpitOperatingLoop("w1", recommendations=[rec()]).build()["workItems"][0]
    c = BusinessCockpitOperatingLoop("w2", recommendations=[rec()]).build()["workItems"][0]
    assert a["itemId"] == b["itemId"]
    assert a["itemId"] != c["itemId"]


def test_decision_link_changes_kind_but_not_authority():
    out = BusinessCockpitOperatingLoop("w1", recommendations=[rec()], decisions=[{"decisionId":"d1","recommendationId":"r1","state":"APPROVED"}]).build()
    item = out["workItems"][0]
    assert item["kind"] == "DECISION"
    assert out["authority"]["workItemsAreCommands"] is False
