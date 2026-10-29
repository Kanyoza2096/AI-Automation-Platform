from services.sector_action_governance_readiness import build_sector_action_governance_readiness


def test_readiness_covers_all_declared_actions_without_granting_authority():
    result = build_sector_action_governance_readiness()
    assert result["count"] == 43
    assert result["summary"]["declared_actions"] == 43
    assert result["authority"]["governance_readiness_grants_execution_authority"] is False


def test_readiness_does_not_invent_governance_or_provider_contracts():
    result = build_sector_action_governance_readiness()
    for row in result["actions"]:
        if row["sector_action"] == "publish_content":
            assert row["governance_contract"]["risk_level"] == "high"
            assert row["governance_contract"]["requires_approval"] is True
            assert row["connector"]["binding"] == "BOUND"
            assert row["receipt"]["protocol"] == "ProviderReceipt"
            assert row["registration_status"] == "READY_FOR_CANONICAL_EXECUTION_PATH"
        elif row["sector_action"] in {"it.request_deployment", "finance.initiate_payment", "commerce.capture_payment", "commerce.fulfill_order", "it.remediate_service", "manufacturing.request_maintenance", "energy.dispatch_crew", "government.notify_applicant", "government.dispatch_field_service", "finance.send_statement", "energy.dispatch_crew"}:
            assert row["governance_contract"]["risk_level"] in {"critical", "high"}
            assert row["governance_contract"]["requires_approval"] is True
            assert row["connector"]["binding"] == "BOUND"
            assert row["receipt"]["protocol"] == "ProviderReceipt"
            assert row["registration_status"] == "READY_FOR_CANONICAL_EXECUTION_PATH"
        elif row["sector_action"] in {"marketing_schedule_content", "commerce.record_payment", "commerce.record_fulfillment", "commerce.record_outcome", "security.record_response_outcome", "security.record_outcome", "crm.record_activity", "crm.record_outcome", "education.record_intervention", "education.record_outcome", "healthcare.record_care_plan", "healthcare.record_outcome", "supply_chain.record_exception", "supply_chain.record_outcome", "energy.record_exception", "energy.record_outcome", "government.record_resolution", "government.record_outcome", "agriculture.record_exception", "agriculture.record_outcome"}:
            assert row["governance_contract"]["risk_level"] in {"low", "medium"}
            assert row["connector"]["binding"] == "NOT_REQUIRED_INTERNAL"
            assert row["receipt"]["protocol"] == "KDOInternalDomainReceipt"
            assert "durable" in row["verification"]["strategy"] and "identity" in row["verification"]["strategy"]
            assert row["reconciliation"] == "NOT_REQUIRED_INTERNAL"
            assert row["registration_status"] == "READY_FOR_CANONICAL_EXECUTION_PATH"
        elif row["sector_action"] in {"crm.send_message", "education.send_parent_message", "healthcare.send_patient_message", "commerce.send_order_notification"}:
            assert row["governance_contract"]["risk_level"] == "high"
            assert row["governance_contract"]["requires_approval"] is True
            assert row["connector"]["binding"] == "BOUND"
            assert row["receipt"]["protocol"] == "ProviderReceipt"
            assert row["registration_status"] == "READY_FOR_CANONICAL_EXECUTION_PATH"
        else:
            assert row["governance_contract"]["risk_level"] == "UNDECLARED"
            assert row["governance_contract"]["requires_approval"] == "UNDECLARED"
            assert row["connector"]["binding"] == "NOT_EXPOSED"
            assert row["receipt"] == "NOT_EXPOSED"
            assert row["verification"] == "NOT_EXPOSED"
            assert row["reconciliation"] == "NOT_EXPOSED"
            assert row["registration_status"].startswith("BLOCKED_PENDING_")


def test_publish_content_keeps_explicit_governed_action_name():
    result = build_sector_action_governance_readiness()
    row = next(x for x in result["actions"] if x["sector_action"] == "publish_content")
    assert row["governed_action_type"] == "publish_content"


def test_publish_content_and_it_deployment_are_currently_ready_sector_actions():
    result = build_sector_action_governance_readiness()
    ready = [x["sector_action"] for x in result["actions"] if x["registration_status"] == "READY_FOR_CANONICAL_EXECUTION_PATH"]
    assert ready == ["publish_content", "marketing_schedule_content", "commerce.capture_payment", "commerce.fulfill_order", "commerce.send_order_notification", "commerce.record_payment", "commerce.record_fulfillment", "commerce.record_outcome", "it.request_deployment", "it.remediate_service", "security.record_response_outcome", "security.record_outcome", "crm.send_message", "crm.record_activity", "crm.record_outcome", "education.send_parent_message", "education.record_intervention", "education.record_outcome", "healthcare.send_patient_message", "healthcare.record_care_plan", "healthcare.record_outcome", "finance.initiate_payment", "finance.send_statement", "supply_chain.record_exception", "supply_chain.record_outcome", "manufacturing.request_maintenance", "energy.dispatch_crew", "energy.record_exception", "energy.record_outcome", "government.notify_applicant", "government.dispatch_field_service", "government.record_resolution", "government.record_outcome", "agriculture.record_exception", "agriculture.record_outcome"]
    assert result["summary"]["ready_for_canonical_execution_path"] == 35
