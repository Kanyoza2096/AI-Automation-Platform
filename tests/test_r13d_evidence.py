from services.evidence_clarification_orchestrator import orchestrate_evidence


def test_policy_prefers_document():
    r = orchestrate_evidence(requirements=["refund policy"])
    assert r["requests"][0]["recommended_channel"] == "document"


def test_service_description_prefers_website():
    r = orchestrate_evidence(requirements=["product/service descriptions"])
    assert r["requests"][0]["recommended_channel"] == "website"


def test_authoritative_records_prefer_connected_source():
    r = orchestrate_evidence(requirements=["authoritative records"])
    assert r["requests"][0]["recommended_channel"] == "connected_source"


def test_evidence_does_not_become_truth():
    r = orchestrate_evidence(requirements=["pricing"], supplied_urls=["https://example.test"])
    assert r["requests"][0]["status"] == "evidence_available"
    assert r["requests"][0]["requires_explicit_confirmation"] is True
    assert r["requests"][0]["creates_knowledge"] is False


def test_unknown_requirement_falls_back_to_clarification():
    r = orchestrate_evidence(requirements=["special business rule"])
    assert r["requests"][0]["recommended_channel"] == "clarify"
    assert r["read_only"] is True
