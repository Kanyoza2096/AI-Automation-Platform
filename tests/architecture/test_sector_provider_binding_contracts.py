from services.sector_provider_binding_contracts import build_sector_provider_binding_contracts


def test_binding_contracts_are_explicit_and_read_only():
    model = build_sector_provider_binding_contracts()
    assert model["read_only"] is True
    assert model["authority"]["sector_pack_grants_execution"] is False
    assert {x["sector_action"] for x in model["bindings"]} == {
        "it.request_deployment", "finance.initiate_payment", "crm.send_message", "education.send_parent_message", "healthcare.send_patient_message", "commerce.send_order_notification", "commerce.capture_payment", "commerce.fulfill_order", "it.remediate_service", "manufacturing.request_maintenance", "government.dispatch_field_service", "finance.send_statement", "energy.dispatch_crew"
    }


def test_render_binding_is_closed_by_identity_version_environment_and_reconciliation_contracts():
    item = next(x for x in build_sector_provider_binding_contracts()["bindings"]
                 if x["sector_action"] == "it.request_deployment")
    assert item["governed_provider_action"] == "render.trigger_deploy"
    assert "SERVICE_IDENTITY_MAPPING" in item["satisfied_gates"]
    assert "VERSION_TO_PROVIDER_REFERENCE_MAPPING" in item["satisfied_gates"]
    assert item["status"] == "BOUND"


def test_finance_binding_does_not_equate_payment_intent_with_terminal_payment():
    item = next(x for x in build_sector_provider_binding_contracts()["bindings"]
                 if x["sector_action"] == "finance.initiate_payment")
    assert item["governed_provider_action"] == "stripe.create_payment_intent"
    assert "TERMINAL_PAYMENT_VERIFICATION" in item["required_gates"]
    assert item["outcome_mapping"]["terminal_state"] != "Stripe payment-intent receipt"
    assert item["status"] == "BOUND"
