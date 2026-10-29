from services.sector_action_contract_registry import build_sector_action_contract_registry
from services.vnext_sector_platform_read_model import PORTFOLIO


def test_every_declared_external_action_is_accounted_for_exactly_once():
    registry = build_sector_action_contract_registry()
    actions = [x["sector_action"] for x in registry["contracts"]]
    assert len(actions) == len(set(actions))
    assert registry["count"] == registry["coverage"]["declared_actions"]
    assert registry["count"] == 43


def test_registry_preserves_explicit_governed_action_mapping_and_code_backed_execution_evidence():
    registry = build_sector_action_contract_registry()
    by_action = {x["sector_action"]: x for x in registry["contracts"]}

    assert by_action["commerce.capture_payment"]["governed_action_type"] == "commerce_capture_payment"
    assert by_action["finance.initiate_payment"]["governed_action_type"] == "finance_initiate_payment"
    capture = by_action["commerce.capture_payment"]
    assert capture["action_kernel"]["binding"] == "BOUND"
    assert capture["governance"]["risk_level"] == "critical"
    assert capture["connector"]["provider_action"] == "stripe.capture_payment_intent"
    assert capture["receipt"]["outcome"] == "uncertain_until_capture_reconciled"
    assert capture["verification"]["strategy"] == "StripeAdapter.get_payment_intent"
    assert capture["reconciliation"]["strategy"] == "CommercePaymentReconciliationService"
    assert by_action["publish_content"]["action_kernel"]["binding"] == "BOUND"
    assert by_action["publish_content"]["governance"]["requires_approval"] is True

    it = by_action["it.request_deployment"]
    assert it["action_kernel"]["binding"] == "BOUND"
    assert it["governance"]["risk_level"] == "critical"
    assert it["governance"]["requires_approval"] is True
    assert it["connector"]["provider_action"] == "render.trigger_deploy"
    assert it["receipt"]["outcome"] == "uncertain_until_reconciled"
    assert it["verification"]["strategy"] == "RenderAdapter.reconcile_deploy"
    assert it["reconciliation"]["strategy"] == "ITDeploymentReconciliationService"

    fulfillment = by_action["commerce.fulfill_order"]
    assert fulfillment["action_kernel"]["binding"] == "BOUND"
    assert fulfillment["governance"]["risk_level"] == "critical"
    assert fulfillment["connector"]["provider_action"] == "shopify.create_fulfillment"
    assert fulfillment["receipt"]["outcome"] == "uncertain_until_fulfillment_reconciled"
    assert fulfillment["verification"]["strategy"] == "ShopifyAdapter.reconcile_fulfillment"
    assert fulfillment["reconciliation"]["strategy"] == "CommerceFulfillmentReconciliationService"

    finance = by_action["finance.initiate_payment"]
    assert finance["action_kernel"]["binding"] == "BOUND"
    assert finance["governance"]["risk_level"] == "critical"
    assert finance["governance"]["requires_approval"] is True
    assert finance["connector"]["provider_action"] == "stripe.create_payment_intent"
    assert finance["receipt"]["outcome"] == "uncertain_until_payment_lifecycle_reconciled"
    assert finance["verification"]["strategy"] == "StripeAdapter.get_payment_intent"
    assert finance["reconciliation"]["strategy"] == "FinancePaymentReconciliationService"

    crm = by_action["crm.send_message"]
    assert crm["action_kernel"]["binding"] == "BOUND"
    assert crm["governance"]["risk_level"] == "high"
    assert crm["governance"]["requires_approval"] is True
    assert crm["connector"]["provider_action"] == "twilio.send_sms"
    assert crm["receipt"]["outcome"] == "uncertain_until_delivery_reconciled"
    assert crm["verification"]["strategy"] == "TwilioAdapter.get_message"
    assert crm["reconciliation"]["strategy"] == "CRMMessageReconciliationService"

    publish = by_action["publish_content"]
    assert publish["connector"]["binding"] == "BOUND"
    assert publish["connector"]["capability"] == "publish"
    assert publish["connector"]["provider_count"] == 6
    assert publish["receipt"]["protocol"] == "ProviderReceipt"
    assert publish["verification"]["code_backed"] is True
    assert publish["reconciliation"]["strategy"] == "ExternalReconciler.reconcile or provider receipt strategy"

    internal = {"marketing_schedule_content", "commerce.record_payment", "commerce.record_fulfillment", "commerce.record_outcome", "security.record_response_outcome", "security.record_outcome", "crm.record_activity", "crm.record_outcome", "education.record_intervention", "education.record_outcome", "healthcare.record_care_plan", "healthcare.record_outcome", "supply_chain.record_exception", "supply_chain.record_outcome", "energy.record_exception", "energy.record_outcome", "government.record_resolution", "government.record_outcome", "agriculture.record_exception", "agriculture.record_outcome"}
    for contract in registry["contracts"]:
        if contract["sector_action"] in internal:
            assert contract["execution_kind"] == "INTERNAL_DOMAIN"
            assert contract["connector"]["binding"] == "NOT_REQUIRED_INTERNAL"
            assert contract["receipt"]["protocol"] == "KDOInternalDomainReceipt"
            continue
        if contract["sector_action"] in {"publish_content", "it.request_deployment", "finance.initiate_payment", "crm.send_message", "education.send_parent_message", "healthcare.send_patient_message", "commerce.send_order_notification", "commerce.capture_payment", "commerce.fulfill_order", "it.remediate_service", "manufacturing.request_maintenance", "government.notify_applicant", "government.dispatch_field_service", "finance.send_statement", "energy.dispatch_crew"}:
            continue
        assert contract["connector"]["binding"] == "NOT_EXPOSED"
        assert contract["receipt"] == "NOT_EXPOSED"
        assert contract["verification"] == "NOT_EXPOSED"
        assert contract["reconciliation"] == "NOT_EXPOSED"


def test_all_canonical_packs_are_represented():
    registry = build_sector_action_contract_registry()
    assert {x["pack_id"] for x in registry["contracts"]} == {x[0] for x in PORTFOLIO}


def test_missing_action_kernel_registration_is_explicit():
    registry = build_sector_action_contract_registry()
    missing = [x for x in registry["contracts"] if x["action_kernel"]["binding"] == "NOT_REGISTERED"]
    assert missing
    assert all(x["governance"]["risk_level"] == "UNDECLARED" for x in missing)
