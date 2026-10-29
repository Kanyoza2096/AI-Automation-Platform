from services.agent_runtime_hardening import prepare_runtime_admission


def _eval(state="READY", key="pricing"):
    return {"requirements": [{"knowledge_key": key, "state": state}]}


def _fact(key="pricing", value="MWK 10,000", status="confirmed"):
    return {
        "id": "f1", "knowledge_key": key, "value": value, "status": status,
        "source": "operator", "source_span": "pricing statement", "extraction_id": "x1",
    }


def test_ready_confirmed_fact_is_admitted_with_citation():
    r = prepare_runtime_admission(
        workspace_id="ws1", question="What is the price?", knowledge_keys=["pricing"],
        evaluation=_eval(), facts=[_fact()],
    )
    assert r["status"] == "ADMITTED"
    assert r["context"][0]["value"] == "MWK 10,000"
    assert r["citations"][0]["fact_id"] == "f1"


def test_missing_knowledge_blocks_runtime():
    r = prepare_runtime_admission(
        workspace_id="ws1", question="What is the price?", knowledge_keys=["pricing"],
        evaluation=_eval("MISSING"), facts=[],
    )
    assert r["status"] == "BLOCKED"
    assert r["admitted"] is False


def test_conflict_blocks_even_if_facts_exist():
    r = prepare_runtime_admission(
        workspace_id="ws1", question="What is the price?", knowledge_keys=["pricing"],
        evaluation=_eval("CONFLICT"), facts=[_fact(value="10"), _fact(value="15")],
    )
    assert r["status"] == "BLOCKED"
    assert r["blocked_requirements"][0]["state"] == "CONFLICT"


def test_unconfirmed_fact_never_enters_context():
    r = prepare_runtime_admission(
        workspace_id="ws1", question="What is the price?", knowledge_keys=["pricing"],
        evaluation=_eval(), facts=[_fact(status="candidate")],
    )
    assert r["status"] == "BLOCKED"


def test_superseded_fact_never_enters_context():
    r = prepare_runtime_admission(
        workspace_id="ws1", question="What is the price?", knowledge_keys=["pricing"],
        evaluation=_eval(), facts=[_fact(status="superseded")],
    )
    assert r["status"] == "BLOCKED"


def test_freshness_requirement_blocks_stale_fact():
    fact = _fact()
    fact["freshness"] = "stale"
    r = prepare_runtime_admission(
        workspace_id="ws1", question="What is the current price?", knowledge_keys=["pricing"],
        evaluation=_eval(), facts=[fact], require_fresh=True,
    )
    assert r["status"] == "BLOCKED"
    assert r["blocked_requirements"][0]["state"] == "STALE_OR_UNKNOWN_FRESHNESS"


def test_unrelated_fact_is_not_leaked_into_runtime_context():
    r = prepare_runtime_admission(
        workspace_id="ws1", question="What is the price?", knowledge_keys=["pricing"],
        evaluation=_eval(), facts=[_fact(), _fact(key="refund_policy", value="30 days")],
    )
    assert r["status"] == "ADMITTED"
    assert [x["knowledge_key"] for x in r["context"]] == ["pricing"]


def test_safety_contract_is_read_only_and_keeps_existing_runtime_authority():
    r = prepare_runtime_admission(
        workspace_id="ws1", question="What is the price?", knowledge_keys=["pricing"],
        evaluation=_eval(), facts=[_fact()],
    )
    assert all(r["safety"].values())
    assert r["runtime_authority"] == "existing_agent_runtime"
