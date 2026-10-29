from datetime import datetime, timezone, timedelta
from services.knowledge_evaluation import evaluate_knowledge


def test_missing_requirement():
    r = evaluate_knowledge([], ["pricing"])
    assert r["requirements"][0]["state"] == "MISSING"
    assert r["requirements"][0]["next_action"] == "discover_and_request_evidence"


def test_ready_requires_confirmation_and_evidence():
    r = evaluate_knowledge([{"id":"1","knowledge_key":"pricing","value":"10","status":"confirmed","evidence_ids":["e1"]}], ["pricing"])
    assert r["requirements"][0]["state"] == "READY"
    assert r["summary"]["requirement_coverage"] == 1.0


def test_unconfirmed_is_not_ready():
    r = evaluate_knowledge([{"id":"1","knowledge_key":"pricing","value":"10","status":"candidate","evidence_ids":["e1"]}], ["pricing"])
    assert r["requirements"][0]["state"] == "UNCONFIRMED"


def test_confirmed_without_evidence_is_flagged():
    r = evaluate_knowledge([{"id":"1","knowledge_key":"pricing","value":"10","status":"confirmed"}], ["pricing"])
    assert r["requirements"][0]["state"] == "CONFIRMED_UNSUPPORTED"
    assert r["summary"]["unsupported_confirmed_count"] == 1


def test_conflict_is_not_ready():
    facts = [
        {"id":"1","knowledge_key":"pricing","value":"10","status":"confirmed","evidence_ids":["e1"]},
        {"id":"2","knowledge_key":"pricing","value":"15","status":"confirmed","evidence_ids":["e2"]},
    ]
    r = evaluate_knowledge(facts, ["pricing"])
    assert r["requirements"][0]["state"] == "CONFLICT"
    assert r["summary"]["conflict_count"] == 1


def test_stale_confirmed_fact_is_attention_item():
    old = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
    r = evaluate_knowledge([{"id":"1","knowledge_key":"hours","value":"8-5","status":"confirmed","evidence_ids":["e1"],"updated_at":old}], ["hours"])
    assert r["summary"]["stale_confirmed_count"] == 1
    assert r["requirements"][0]["state"] == "READY"


def test_superseded_fact_does_not_count():
    r = evaluate_knowledge([{"id":"old","knowledge_key":"pricing","value":"5","status":"superseded"}], ["pricing"])
    assert r["requirements"][0]["state"] == "MISSING"


def test_read_only_safety():
    r = evaluate_knowledge([], [])
    assert all(r["safety"].values())
