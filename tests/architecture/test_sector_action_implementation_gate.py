from services.sector_action_implementation_gate import build_sector_action_implementation_gate


def test_implementation_gate_covers_all_sector_actions_without_granting_authority():
    result = build_sector_action_implementation_gate()
    assert result["read_only"] is True
    assert result["summary"]["declared_actions"] == 43
    assert result["summary"]["ready"] >= 1
    assert result["summary"]["ready"] + result["summary"]["blocked"] == 43
    assert result["authority"]["sector_pack_grants_execution"] is False


def test_currently_complete_gates():
    result = build_sector_action_implementation_gate()
    ready = [x for x in result["actions"] if x["gate"]["status"] == "READY_FOR_CANONICAL_EXECUTION_PATH"]
    assert [x["sector_action"] for x in ready] == ["publish_content", "marketing_schedule_content", "commerce.capture_payment", "commerce.fulfill_order", "commerce.send_order_notification", "commerce.record_payment", "commerce.record_fulfillment", "commerce.record_outcome", "it.request_deployment", "it.remediate_service", "security.record_response_outcome", "security.record_outcome", "crm.send_message", "crm.record_activity", "crm.record_outcome", "education.send_parent_message", "education.record_intervention", "education.record_outcome", "healthcare.send_patient_message", "healthcare.record_care_plan", "healthcare.record_outcome", "finance.initiate_payment", "finance.send_statement", "supply_chain.record_exception", "supply_chain.record_outcome", "manufacturing.request_maintenance", "energy.dispatch_crew", "energy.record_exception", "energy.record_outcome", "government.notify_applicant", "government.dispatch_field_service", "government.record_resolution", "government.record_outcome", "agriculture.record_exception", "agriculture.record_outcome"]


def test_blocked_actions_expose_concrete_first_gate_without_inventing_contracts():
    result = build_sector_action_implementation_gate()
    blocked = [x for x in result["actions"] if x["gate"]["status"] == "BLOCKED"]
    assert blocked
    for item in blocked:
        assert item["gate"]["blockers"]
        assert item["gate"]["next_engineering_gate"] in item["gate"]["blockers"]


def test_provider_candidates_are_discovery_only_and_keep_unresolved_semantics_explicit():
    result = build_sector_action_implementation_gate()
    by_action = {x["sector_action"]: x for x in result["actions"]}
    render = by_action["it.request_deployment"]["provider_candidates"][0]
    assert render["provider_action"] == "render.trigger_deploy"
    assert render["status"] == "CANDIDATE_BOUND"
    assert render["blockers"] == []
    stripe = by_action["finance.initiate_payment"]["provider_candidates"][0]
    assert stripe["provider_action"] == "stripe.create_payment_intent"
    assert stripe["status"] == "CANDIDATE_BOUND"
    assert stripe["blockers"] == []
    github = by_action["it.remediate_service"]["provider_candidates"][0]
    assert github["provider_action"] == "github.dispatch_workflow"
    assert github["status"] == "CANDIDATE_BOUND"
    assert github["blockers"] == []
    assert by_action["it.request_deployment"]["gate"]["status"] == "READY_FOR_CANONICAL_EXECUTION_PATH"
    assert by_action["finance.initiate_payment"]["gate"]["status"] == "READY_FOR_CANONICAL_EXECUTION_PATH"
    assert by_action["it.remediate_service"]["gate"]["status"] == "READY_FOR_CANONICAL_EXECUTION_PATH"
