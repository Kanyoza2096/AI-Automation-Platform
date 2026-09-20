from services.v70_6_content_marketing_operating_loop import (
    ContentBrief, ContentLifecycle, ContentMarketingOperatingLoop,
    MarketingDisposition, MarketingObjective,
)


def make_loop():
    loop = ContentMarketingOperatingLoop()
    loop.register_objective(MarketingObjective(
        objective_id="obj-1", workspace_id="ws-1", name="Demand generation",
        outcome="Generate qualified enquiries", audience="SMBs",
        channels=("facebook", "linkedin"),
    ))
    loop.create_brief(ContentBrief(
        brief_id="brief-1", workspace_id="ws-1", objective_id="obj-1",
        topic="Governed AI for business", audience="SMBs",
        channels=("facebook", "linkedin"), correlation_id="corr-1",
    ))
    return loop


def test_admission_is_governed_and_requires_intelligence_and_channel():
    loop = make_loop()
    assert loop.admit("brief-1").disposition == MarketingDisposition.READY
    assert loop.admit("brief-1", intelligence_available=False).disposition == MarketingDisposition.ADVISORY_ONLY
    loop.create_brief(ContentBrief("brief-2", "ws-1", "obj-1", "x", channels=()))
    assert loop.admit("brief-2").disposition == MarketingDisposition.BLOCKED


def test_candidate_is_deterministic_and_workspace_bound():
    loop = make_loop()
    a = loop.create_candidate("brief-1", caption="Evidence-led post", confidence=1.2, evidence_fingerprints=["e1"])
    b = loop.create_candidate("brief-1", caption="Evidence-led post", confidence=.9, evidence_fingerprints=["e1"])
    assert a.candidate_id == b.candidate_id
    assert a.confidence == 1.0
    assert a.workspace_id == "ws-1"


def test_publish_request_never_executes_and_requires_decision():
    loop = make_loop()
    c = loop.create_candidate("brief-1", caption="Safe draft")
    try:
        loop.prepare_publish_request(c.candidate_id, platform="facebook", decision_id="")
        assert False
    except ValueError as exc:
        assert "decision_id" in str(exc)
    req = loop.prepare_publish_request(c.candidate_id, platform="facebook", decision_id="dec-1")
    assert req["action"] == "publish_content"
    assert req["decision_id"] == "dec-1"
    assert loop.get_candidate(c.candidate_id).lifecycle == ContentLifecycle.GOVERNANCE_PENDING


def test_platform_must_be_in_brief():
    loop = make_loop()
    c = loop.create_candidate("brief-1", caption="Safe draft")
    try:
        loop.prepare_publish_request(c.candidate_id, platform="tiktok", decision_id="dec-1")
        assert False
    except ValueError as exc:
        assert "platform_not_authorized" in str(exc)


def test_reconciliation_is_result_driven():
    loop = make_loop()
    c = loop.create_candidate("brief-1", caption="Safe draft")
    loop.prepare_publish_request(c.candidate_id, platform="facebook", decision_id="dec-1")
    loop.reconcile_publish_result(c.candidate_id, success=True, provider_result={"post_id": "p1"})
    assert loop.get_candidate(c.candidate_id).lifecycle == ContentLifecycle.PUBLISHED
