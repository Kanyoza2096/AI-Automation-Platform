"""Canonical sector action-contract registry.

This registry is a domain-contract layer only. It never executes actions and
never grants provider authority. It records the explicit mapping from a
Sector Pack action declaration to its governed Action Kernel action type and
surfaces downstream connector/receipt/verification/reconciliation states.
Missing implementation evidence is represented explicitly instead of being
inferred.
"""
from __future__ import annotations

from importlib import import_module
from typing import Any

from services.action_kernel import build_default_action_kernel
from services.vnext_sector_platform_read_model import PORTFOLIO
from services.vnext_connector_fabric_read_model import connector_fabric_catalog
from domain.publishing.provider_contract import CONTRACTS as PUBLISHING_PROVIDER_CONTRACTS
from services.sector_it_deployment_execution import IT_DEPLOYMENT_ACTION_CONTRACT_SPEC
from services.sector_finance_payment_execution import FINANCE_PAYMENT_ACTION_CONTRACT_SPEC
from services.sector_finance_statement_execution import FINANCE_STATEMENT_ACTION_CONTRACT_SPEC
from services.sector_crm_message_execution import CRM_MESSAGE_ACTION_CONTRACT_SPEC
from services.sector_education_parent_message_execution import EDUCATION_PARENT_MESSAGE_ACTION_CONTRACT_SPEC
from services.sector_healthcare_patient_message_execution import HEALTHCARE_PATIENT_MESSAGE_ACTION_CONTRACT_SPEC
from services.sector_commerce_order_notification_execution import COMMERCE_ORDER_NOTIFICATION_ACTION_CONTRACT_SPEC
from services.sector_commerce_capture_payment_execution import COMMERCE_CAPTURE_PAYMENT_ACTION_CONTRACT_SPEC
from services.sector_commerce_fulfillment_execution import COMMERCE_FULFILL_ORDER_ACTION_CONTRACT_SPEC
from services.sector_it_service_remediation_execution import IT_SERVICE_REMEDIATION_ACTION_CONTRACT_SPEC
from services.sector_manufacturing_maintenance_execution import MANUFACTURING_MAINTENANCE_ACTION_CONTRACT_SPEC
from services.sector_government_applicant_notification_execution import GOVERNMENT_APPLICANT_NOTIFICATION_ACTION_CONTRACT_SPEC
from services.sector_government_field_service_execution import GOVERNMENT_FIELD_SERVICE_ACTION_CONTRACT_SPEC
from services.sector_energy_crew_dispatch_execution import ENERGY_CREW_DISPATCH_ACTION_CONTRACT_SPEC
from services.sector_commerce_internal_record_execution import (
    COMMERCE_RECORD_PAYMENT_ACTION_CONTRACT_SPEC,
    COMMERCE_RECORD_FULFILLMENT_ACTION_CONTRACT_SPEC,
    COMMERCE_RECORD_OUTCOME_ACTION_CONTRACT_SPEC,
)
from services.sector_marketing_schedule_execution import MARKETING_SCHEDULE_CONTENT_ACTION_CONTRACT_SPEC
from services.sector_security_internal_record_execution import (
    SECURITY_RECORD_RESPONSE_OUTCOME_ACTION_CONTRACT_SPEC,
    SECURITY_RECORD_OUTCOME_ACTION_CONTRACT_SPEC,
)
from services.sector_internal_record_execution import ACTION_CONTRACT_SPECS


def _plugin(module_name: str, class_name: str):
    module = import_module(module_name)
    return getattr(module, class_name)(None, None)


def _command_metadata(plugin: Any) -> dict[str, dict[str, Any]]:
    commands = getattr(plugin, "commands_handler", None)
    if commands is None:
        return {}
    listing = commands.list_all() if callable(getattr(commands, "list_all", None)) else []
    return {
        str(item.get("intent")): item
        for item in listing
        if isinstance(item, dict) and item.get("intent")
    }



def _publishing_connector_evidence() -> dict[str, Any]:
    """Return code-backed Connector Fabric evidence for canonical publishing.

    The sector action is platform-dynamic, so evidence is expressed as the
    supported provider set rather than pretending there is one provider.
    No adapter is instantiated and no provider is contacted.
    """
    social = {r.connector_key: r for r in connector_fabric_catalog() if r.connector_key in PUBLISHING_PROVIDER_CONTRACTS}
    providers = []
    for provider, contract in PUBLISHING_PROVIDER_CONTRACTS.items():
        record = social.get(provider)
        if record is None:
            continue
        lookup = "authoritative_lookup:lookup_post" if "lookup" in record.capabilities else "receipt_identity"
        reconciliation = "ExternalReconciler.reconcile" if "lookup" in record.capabilities else contract.reconciliation
        providers.append({
            "provider": provider,
            "connector_key": record.connector_key,
            "capability": "publish",
            "provider_contract": {
                "receipt_kind": contract.receipt_kind,
                "reconciliation": contract.reconciliation,
            },
            "receipt": "ProviderReceipt via receipt_from_result",
            "verification": lookup,
            "reconciliation": reconciliation,
        })
    return {
        "binding": "BOUND",
        "capability": "publish",
        "provider_count": len(providers),
        "providers": providers,
        "adapter_instantiation": False,
        "provider_contact": False,
        "authority": "CONNECTOR_FABRIC",
    }

def _commerce_capture_payment_evidence() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    return (
        {"binding":"BOUND","provider":"stripe","capability":"payment_capture","provider_action":"stripe.capture_payment_intent","authority":"CONNECTOR_FABRIC","code_backed":True},
        {"protocol":"ProviderReceipt","translation":"StripeAdapter.capture_payment_intent","outcome":"uncertain_until_capture_reconciled","code_backed":True},
        {"strategy":"StripeAdapter.get_payment_intent","terminal_provider_states":["succeeded","canceled"],"business_outcome_promotion":"only after authoritative capture reconciliation","code_backed":True},
        {"strategy":"CommercePaymentReconciliationService","durable_evidence":"commerce_payment_reconciliation_evidence","code_backed":True},
    )


def _finance_payment_evidence() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    connector = {
        "binding": "BOUND",
        "provider": "stripe",
        "capability": "payment_intent_create",
        "provider_action": "stripe.create_payment_intent",
        "authority": "CONNECTOR_FABRIC",
        "code_backed": True,
    }
    receipt = {
        "protocol": "ProviderReceipt",
        "translation": "StripeAdapter.create_payment_intent",
        "outcome": "uncertain_until_payment_lifecycle_reconciled",
        "code_backed": True,
    }
    verification = {
        "strategy": "StripeAdapter.get_payment_intent",
        "terminal_provider_states": ["succeeded", "canceled"],
        "business_outcome_promotion": "only after authoritative lifecycle reconciliation",
        "code_backed": True,
    }
    reconciliation = {
        "strategy": "FinancePaymentReconciliationService",
        "durable_evidence": "finance_payment_reconciliation_evidence",
        "outcome": "finance.record_financial_outcome after terminal verification",
        "code_backed": True,
    }
    return connector, receipt, verification, reconciliation


def _crm_message_evidence() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    connector = {
        "binding": "BOUND",
        "provider": "twilio",
        "capability": "sms_send",
        "provider_action": "twilio.send_sms",
        "authority": "CONNECTOR_FABRIC",
        "code_backed": True,
    }
    receipt = {
        "protocol": "ProviderReceipt",
        "translation": "TwilioAdapter.send_sms",
        "outcome": "uncertain_until_delivery_reconciled",
        "code_backed": True,
    }
    verification = {
        "strategy": "TwilioAdapter.get_message",
        "terminal_provider_states": ["delivered", "received", "undelivered", "failed", "canceled", "cancelled"],
        "business_outcome_promotion": "only after authoritative provider delivery reconciliation",
        "code_backed": True,
    }
    reconciliation = {
        "strategy": "CRMMessageReconciliationService",
        "orchestrator": "ReconciliationOrchestrator.reconcile_crm_message",
        "scheduler": "CRMMessageReconciliationScheduler",
        "durable_evidence": "crm_message_reconciliation_evidence",
        "code_backed": True,
    }
    return connector, receipt, verification, reconciliation

def _education_parent_message_evidence() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    return (
        {"binding":"BOUND","provider":"twilio","capability":"sms_send","provider_action":"twilio.send_sms","authority":"CONNECTOR_FABRIC","code_backed":True},
        {"protocol":"ProviderReceipt","translation":"TwilioAdapter.send_sms","outcome":"uncertain_until_parent_delivery_reconciled","code_backed":True},
        {"strategy":"TwilioAdapter.get_message","terminal_provider_states":["delivered","received","undelivered","failed","canceled","cancelled"],"business_outcome_promotion":"only after authoritative provider delivery reconciliation","code_backed":True},
        {"strategy":"EducationParentMessageReconciliationService","orchestrator":"ReconciliationOrchestrator.reconcile_education_parent_message","scheduler":"EducationParentMessageReconciliationScheduler","durable_evidence":"education_parent_message_reconciliation_evidence","code_backed":True},
    )

def _healthcare_patient_message_evidence() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    return (
        {"binding":"BOUND","provider":"twilio","capability":"sms_send","provider_action":"twilio.send_sms","authority":"CONNECTOR_FABRIC","code_backed":True},
        {"protocol":"ProviderReceipt","translation":"TwilioAdapter.send_sms","outcome":"uncertain_until_patient_delivery_reconciled","code_backed":True},
        {"strategy":"TwilioAdapter.get_message","terminal_provider_states":["delivered","received","undelivered","failed","canceled","cancelled"],"business_outcome_promotion":"only after authoritative provider delivery reconciliation","code_backed":True},
        {"strategy":"HealthcarePatientMessageReconciliationService","orchestrator":"ReconciliationOrchestrator.reconcile_healthcare_patient_message","scheduler":"HealthcarePatientMessageReconciliationScheduler","durable_evidence":"healthcare_patient_message_reconciliation_evidence","code_backed":True},
    )

def _commerce_fulfill_order_evidence() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    return (
        {"binding":"BOUND","provider":"shopify","capability":"fulfillment_create","provider_action":"shopify.create_fulfillment","authority":"CONNECTOR_FABRIC","code_backed":True},
        {"protocol":"ProviderReceipt","translation":"ShopifyAdapter.create_fulfillment","outcome":"uncertain_until_fulfillment_reconciled","code_backed":True},
        {"strategy":"ShopifyAdapter.reconcile_fulfillment","terminal_provider_states":["SUCCESS","FAILURE","ERROR","CANCELLED"],"business_outcome_promotion":"only after authoritative fulfillment reconciliation","code_backed":True},
        {"strategy":"CommerceFulfillmentReconciliationService","orchestrator":"ReconciliationOrchestrator.reconcile_commerce_fulfillment","durable_evidence":"commerce_fulfillment_reconciliation_evidence","code_backed":True},
    )

def _it_deployment_evidence() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Return code-backed IT deployment provider/receipt/verification/reconciliation evidence."""
    connector = {
        "binding": "BOUND",
        "provider": "render",
        "capability": "deploy_trigger",
        "provider_action": "render.trigger_deploy",
        "authority": "CONNECTOR_FABRIC",
        "code_backed": True,
    }
    receipt = {
        "protocol": "ProviderReceipt",
        "translation": "RenderAdapter.trigger_deploy",
        "outcome": "uncertain_until_reconciled",
        "code_backed": True,
    }
    verification = {
        "strategy": "RenderAdapter.reconcile_deploy",
        "terminal_states": ["succeeded", "failed"],
        "durable_evidence": "it_deployment_reconciliation_evidence",
        "code_backed": True,
    }
    reconciliation = {
        "strategy": "ITDeploymentReconciliationService",
        "orchestrator": "ReconciliationOrchestrator.reconcile_it_deployment",
        "scheduler": "ITDeploymentReconciliationScheduler",
        "evidence": "it_deployment_reconciliation_evidence",
        "code_backed": True,
    }
    return connector, receipt, verification, reconciliation


def _it_service_remediation_evidence() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    return (
        {"binding":"BOUND","provider":"github","capability":"workflow_dispatch","provider_action":"github.dispatch_workflow","authority":"CONNECTOR_FABRIC","code_backed":True},
        {"protocol":"ProviderReceipt","translation":"GitHubAdapter.dispatch_workflow","outcome":"uncertain_until_workflow_reconciled","code_backed":True},
        {"strategy":"GitHubAdapter.reconcile_workflow","terminal_provider_states":["completed/success","completed/failure","completed/cancelled","completed/timed_out"],"business_outcome_promotion":"only after authoritative workflow-run reconciliation","code_backed":True},
        {"strategy":"ITServiceRemediationReconciliationService","orchestrator":"ReconciliationOrchestrator.reconcile_it_service_remediation","durable_evidence":"it_service_remediation_reconciliation_evidence","code_backed":True},
    )

def _government_applicant_notification_evidence() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    return (
        {"binding":"BOUND","provider":"twilio","capability":"sms_send","provider_action":"twilio.send_sms","authority":"CONNECTOR_FABRIC","code_backed":True},
        {"protocol":"ProviderReceipt","translation":"TwilioAdapter.send_sms","outcome":"uncertain_until_applicant_delivery_reconciled","code_backed":True},
        {"strategy":"TwilioAdapter.get_message","terminal_provider_states":["delivered","received","undelivered","failed","canceled","cancelled"],"business_outcome_promotion":"only after authoritative provider delivery reconciliation","code_backed":True},
        {"strategy":"GovernmentApplicantNotificationReconciliationService","orchestrator":"ReconciliationOrchestrator.reconcile_government_applicant_notification","durable_evidence":"government_applicant_notification_reconciliation_evidence","code_backed":True},
    )

def _manufacturing_maintenance_evidence() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    return (
        {"binding":"BOUND","provider":"jira","capability":"issue_create","provider_action":"jira.create_issue","authority":"CONNECTOR_FABRIC","code_backed":True},
        {"protocol":"ProviderReceipt","translation":"JiraAdapter.create_issue","outcome":"uncertain_until_issue_reconciled","code_backed":True},
        {"strategy":"JiraAdapter.get_issue","terminal_provider_states":["issue_exists"],"business_outcome_promotion":"only after authoritative issue lookup","code_backed":True},
        {"strategy":"ManufacturingMaintenanceReconciliationService","orchestrator":"ReconciliationOrchestrator.reconcile_manufacturing_maintenance","scheduler":"ManufacturingMaintenanceReconciliationScheduler","durable_evidence":"manufacturing_maintenance_reconciliation_evidence","code_backed":True},
    )


def _government_field_service_evidence() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    return (
        {"binding":"BOUND","provider":"jira","capability":"issue_create","provider_action":"jira.create_issue","authority":"CONNECTOR_FABRIC","code_backed":True},
        {"protocol":"ProviderReceipt","translation":"JiraAdapter.create_issue","outcome":"uncertain_until_field_service_reconciled","code_backed":True},
        {"strategy":"JiraAdapter.get_issue","terminal_provider_states":["issue_exists"],"business_outcome_promotion":"only after authoritative issue verification","code_backed":True},
        {"strategy":"GovernmentFieldServiceReconciliationService","durable_evidence":"government_field_service_reconciliation_evidence","outcome":"field-service dispatch request recorded only after provider verification","code_backed":True},
    )

def build_sector_action_contract_registry() -> dict[str, Any]:
    kernel = build_default_action_kernel(db=None, allow_in_memory=True)
    contracts: list[dict[str, Any]] = []
    seen: set[str] = set()

    for pack_id, module_name, class_name in PORTFOLIO:
        plugin = _plugin(module_name, class_name)
        manifest = plugin.get_manifest()
        command_meta = _command_metadata(plugin)

        for declared_action in manifest.external_actions:
            if declared_action in seen:
                raise ValueError(f"duplicate sector external action: {declared_action}")
            seen.add(declared_action)

            # Marketing's publish_content uses the declared action name directly;
            # other packs expose an explicit governed_action_type in their command
            # metadata. We only use values present in source code.
            intent = declared_action.rsplit(".", 1)[-1]
            if declared_action == "publish_content":
                governed_action_type = declared_action
                source = "manifest/action-kernel-aligned"
                command = command_meta.get("publish_content", {})
            else:
                intent = declared_action.rsplit(".", 1)[-1]
                command = command_meta.get(intent, {})
                governed_action_type = command.get("governed_action_type")
                source = "sector-command-declaration" if governed_action_type else "NOT_DECLARED"

            kernel_contract = kernel.get_contract_metadata(governed_action_type) if governed_action_type else None
            if declared_action == "crm.send_message" and kernel_contract is None:
                kernel_contract = dict(CRM_MESSAGE_ACTION_CONTRACT_SPEC)
            if declared_action == "it.request_deployment" and kernel_contract is None:
                # The runtime handler is dependency-injected, so the default
                # metadata kernel cannot register it without provider objects.
                # The canonical contract specification is nevertheless code-backed
                # by the execution bridge and is sufficient for a read-only gate.
                kernel_contract = dict(IT_DEPLOYMENT_ACTION_CONTRACT_SPEC)
            if declared_action == "finance.initiate_payment" and kernel_contract is None:
                kernel_contract = dict(FINANCE_PAYMENT_ACTION_CONTRACT_SPEC)
            if declared_action == "finance.send_statement" and kernel_contract is None:
                kernel_contract = dict(FINANCE_STATEMENT_ACTION_CONTRACT_SPEC)
            if declared_action == "education.send_parent_message" and kernel_contract is None:
                kernel_contract = dict(EDUCATION_PARENT_MESSAGE_ACTION_CONTRACT_SPEC)
            if declared_action == "healthcare.send_patient_message" and kernel_contract is None:
                kernel_contract = dict(HEALTHCARE_PATIENT_MESSAGE_ACTION_CONTRACT_SPEC)
            if declared_action == "commerce.send_order_notification" and kernel_contract is None:
                kernel_contract = dict(COMMERCE_ORDER_NOTIFICATION_ACTION_CONTRACT_SPEC)
            if declared_action == "commerce.capture_payment" and kernel_contract is None:
                kernel_contract = dict(COMMERCE_CAPTURE_PAYMENT_ACTION_CONTRACT_SPEC)
            if declared_action == "commerce.fulfill_order" and kernel_contract is None:
                kernel_contract = dict(COMMERCE_FULFILL_ORDER_ACTION_CONTRACT_SPEC)
            if declared_action == "it.remediate_service" and kernel_contract is None:
                kernel_contract = dict(IT_SERVICE_REMEDIATION_ACTION_CONTRACT_SPEC)
            if declared_action == "manufacturing.request_maintenance" and kernel_contract is None:
                kernel_contract = dict(MANUFACTURING_MAINTENANCE_ACTION_CONTRACT_SPEC)
            if declared_action == "government.notify_applicant" and kernel_contract is None:
                kernel_contract = dict(GOVERNMENT_APPLICANT_NOTIFICATION_ACTION_CONTRACT_SPEC)
            if declared_action == "government.dispatch_field_service" and kernel_contract is None:
                kernel_contract = dict(GOVERNMENT_FIELD_SERVICE_ACTION_CONTRACT_SPEC)
            if declared_action == "energy.dispatch_crew" and kernel_contract is None:
                kernel_contract = dict(ENERGY_CREW_DISPATCH_ACTION_CONTRACT_SPEC)
            if declared_action == "marketing_schedule_content" and kernel_contract is None:
                kernel_contract = dict(MARKETING_SCHEDULE_CONTENT_ACTION_CONTRACT_SPEC)
            if declared_action == "commerce.record_payment" and kernel_contract is None:
                kernel_contract = dict(COMMERCE_RECORD_PAYMENT_ACTION_CONTRACT_SPEC)
            if declared_action == "commerce.record_fulfillment" and kernel_contract is None:
                kernel_contract = dict(COMMERCE_RECORD_FULFILLMENT_ACTION_CONTRACT_SPEC)
            if declared_action == "commerce.record_outcome" and kernel_contract is None:
                kernel_contract = dict(COMMERCE_RECORD_OUTCOME_ACTION_CONTRACT_SPEC)
            if declared_action == "security.record_response_outcome" and kernel_contract is None:
                kernel_contract = dict(SECURITY_RECORD_RESPONSE_OUTCOME_ACTION_CONTRACT_SPEC)
            if declared_action == "security.record_outcome" and kernel_contract is None:
                kernel_contract = dict(SECURITY_RECORD_OUTCOME_ACTION_CONTRACT_SPEC)
            if declared_action.replace(".", "_") in ACTION_CONTRACT_SPECS and kernel_contract is None:
                kernel_contract = dict(ACTION_CONTRACT_SPECS[declared_action.replace(".", "_")])

            if declared_action == "publish_content":
                connector_evidence = _publishing_connector_evidence()
                receipt_evidence = {"protocol": "ProviderReceipt", "translation": "receipt_from_result", "code_backed": True}
                verification_evidence = {"strategy": "authoritative provider lookup or receipt identity", "code_backed": True}
                reconciliation_evidence = {"strategy": "ExternalReconciler.reconcile or provider receipt strategy", "code_backed": True}
            elif declared_action == "it.request_deployment":
                connector_evidence, receipt_evidence, verification_evidence, reconciliation_evidence = _it_deployment_evidence()
            elif declared_action == "finance.initiate_payment":
                connector_evidence, receipt_evidence, verification_evidence, reconciliation_evidence = _finance_payment_evidence()
            elif declared_action == "finance.send_statement":
                connector_evidence = {"binding":"BOUND","provider":"twilio","capability":"sms_send","provider_action":"twilio.send_sms","authority":"CONNECTOR_FABRIC","code_backed":True}
                receipt_evidence = {"protocol":"ProviderReceipt","translation":"TwilioAdapter.send_sms","outcome":"uncertain_until_statement_delivery_reconciled","code_backed":True}
                verification_evidence = {"strategy":"TwilioAdapter.get_message","terminal_provider_states":["delivered","received","undelivered","failed","canceled","cancelled"],"business_outcome_promotion":"only after authoritative statement delivery reconciliation","code_backed":True}
                reconciliation_evidence = {"strategy":"FinanceStatementReconciliationService","orchestrator":"ReconciliationOrchestrator.reconcile_finance_statement","durable_evidence":"finance_statement_reconciliation_evidence","code_backed":True}
            elif declared_action == "crm.send_message":
                connector_evidence, receipt_evidence, verification_evidence, reconciliation_evidence = _crm_message_evidence()
            elif declared_action == "education.send_parent_message":
                connector_evidence, receipt_evidence, verification_evidence, reconciliation_evidence = _education_parent_message_evidence()
            elif declared_action == "healthcare.send_patient_message":
                connector_evidence, receipt_evidence, verification_evidence, reconciliation_evidence = _healthcare_patient_message_evidence()
            elif declared_action == "commerce.capture_payment":
                connector_evidence, receipt_evidence, verification_evidence, reconciliation_evidence = _commerce_capture_payment_evidence()
            elif declared_action == "commerce.fulfill_order":
                connector_evidence, receipt_evidence, verification_evidence, reconciliation_evidence = _commerce_fulfill_order_evidence()
            elif declared_action == "it.remediate_service":
                connector_evidence, receipt_evidence, verification_evidence, reconciliation_evidence = _it_service_remediation_evidence()
            elif declared_action == "manufacturing.request_maintenance":
                connector_evidence, receipt_evidence, verification_evidence, reconciliation_evidence = _manufacturing_maintenance_evidence()
            elif declared_action == "government.notify_applicant":
                connector_evidence, receipt_evidence, verification_evidence, reconciliation_evidence = _government_applicant_notification_evidence()
            elif declared_action == "government.dispatch_field_service":
                connector_evidence, receipt_evidence, verification_evidence, reconciliation_evidence = _government_field_service_evidence()
            elif declared_action == "energy.dispatch_crew":
                connector_evidence = {"binding":"BOUND","provider":"jira","capability":"work_order_dispatch_request","provider_action":"jira.create_issue","authority":"CONNECTOR_FABRIC","code_backed":True}
                receipt_evidence = {"protocol":"ProviderReceipt","translation":"JiraAdapter.create_issue","outcome":"uncertain_until_crew_dispatch_request_reconciled","code_backed":True}
                verification_evidence = {"strategy":"JiraAdapter.get_issue","terminal_provider_states":["issue_observed"],"business_outcome_promotion":"only after authoritative dispatch-request reconciliation","code_backed":True}
                reconciliation_evidence = {"strategy":"EnergyCrewDispatchReconciliationService","orchestrator":"ReconciliationOrchestrator.reconcile_energy_crew_dispatch","durable_evidence":"energy_crew_dispatch_reconciliation_evidence","code_backed":True}
            elif declared_action == "marketing_schedule_content":
                connector_evidence = {"binding":"NOT_REQUIRED_INTERNAL","authority":"ACTION_KERNEL","code_backed":True}
                receipt_evidence = {"protocol":"KDOInternalDomainReceipt","translation":"ActionKernel internal domain write result","outcome":"succeeded_after_durable_domain_write","code_backed":True}
                verification_evidence = {"strategy":"durable marketing repository write + returned ContentSchedule identity","business_outcome_promotion":"only from persisted schedule record","code_backed":True}
                reconciliation_evidence = "NOT_REQUIRED_INTERNAL"
            elif declared_action in {"marketing_schedule_content", "commerce.record_payment", "commerce.record_fulfillment", "commerce.record_outcome", "security.record_response_outcome", "security.record_outcome", "crm.record_activity", "crm.record_outcome", "education.record_intervention", "education.record_outcome", "healthcare.record_care_plan", "healthcare.record_outcome", "supply_chain.record_exception", "supply_chain.record_outcome", "energy.record_exception", "energy.record_outcome", "government.record_resolution", "government.record_outcome", "agriculture.record_exception", "agriculture.record_outcome"}:
                connector_evidence = {"binding":"NOT_REQUIRED_INTERNAL","authority":"ACTION_KERNEL","code_backed":True}
                receipt_evidence = {"protocol":"KDOInternalDomainReceipt","translation":"ActionKernel internal domain write result","outcome":"succeeded_after_durable_domain_write","code_backed":True}
                verification_evidence = {"strategy":"durable repository write + returned domain object identity","business_outcome_promotion":"only from persisted domain record","code_backed":True}
                reconciliation_evidence = "NOT_REQUIRED_INTERNAL"
            elif declared_action == "commerce.send_order_notification":
                connector_evidence = {"binding":"BOUND","provider":"twilio","capability":"sms_send","provider_action":"twilio.send_sms","authority":"CONNECTOR_FABRIC","code_backed":True}
                receipt_evidence = {"protocol":"ProviderReceipt","translation":"TwilioAdapter.send_sms","outcome":"uncertain_until_order_notification_delivery_reconciled","code_backed":True}
                verification_evidence = {"strategy":"TwilioAdapter.get_message","terminal_provider_states":["delivered","received","undelivered","failed","canceled","cancelled"],"business_outcome_promotion":"only after authoritative provider delivery reconciliation","code_backed":True}
                reconciliation_evidence = {"strategy":"CommerceOrderNotificationReconciliationService","orchestrator":"ReconciliationOrchestrator.reconcile_commerce_order_notification","scheduler":"CommerceOrderNotificationReconciliationScheduler","durable_evidence":"commerce_order_notification_reconciliation_evidence","code_backed":True}
            else:
                connector_evidence = {"binding": "NOT_EXPOSED", "provider": "NOT_EXPOSED", "capability": "NOT_EXPOSED"}
                receipt_evidence = "NOT_EXPOSED"
                verification_evidence = "NOT_EXPOSED"
                reconciliation_evidence = "NOT_EXPOSED"

            contracts.append({
                "pack_id": pack_id,
                "sector_action": declared_action,
                "capability": intent,
                "governed_action_type": governed_action_type,
                "governed_action_source": source,
                "required_parameters": list(command.get("params") or (kernel_contract or {}).get("required_parameters") or []),
                "execution_mode_declared": command.get("execution_mode", "NOT_DECLARED"),
                "execution_kind": "INTERNAL_DOMAIN" if declared_action in {"marketing_schedule_content", "commerce.record_payment", "commerce.record_fulfillment", "commerce.record_outcome", "security.record_response_outcome", "security.record_outcome", "crm.record_activity", "crm.record_outcome", "education.record_intervention", "education.record_outcome", "healthcare.record_care_plan", "healthcare.record_outcome", "supply_chain.record_exception", "supply_chain.record_outcome", "energy.record_exception", "energy.record_outcome", "government.record_resolution", "government.record_outcome", "agriculture.record_exception", "agriculture.record_outcome"} else "EXTERNAL_PROVIDER",
                "action_kernel": {
                    "binding": "BOUND" if kernel_contract else "NOT_REGISTERED",
                    "contract": kernel_contract,
                },
                "governance": {
                    "risk_level": kernel_contract.get("risk_level") if kernel_contract else "UNDECLARED",
                    "requires_approval": kernel_contract.get("requires_approval") if kernel_contract else "UNDECLARED",
                    "irreversible": kernel_contract.get("irreversible") if kernel_contract else "UNDECLARED",
                    "authorization_policy": "ACTION_KERNEL_CONTRACT" if kernel_contract else "NOT_EXPOSED",
                },
                "connector": connector_evidence,
                "receipt": receipt_evidence,
                "verification": verification_evidence,
                "reconciliation": reconciliation_evidence,
            })

    return {
        "schema": "KDO.SectorActionContractRegistry/v1",
        "portfolio_version": "1.1",
        "read_only": True,
        "authority": {
            "sector_pack_grants_execution": False,
            "action_kernel_is_execution_authority": True,
            "connector_fabric_is_provider_authority": True,
        },
        "count": len(contracts),
        "contracts": contracts,
        "coverage": {
            "declared_actions": len(contracts),
            "action_kernel_bound": sum(1 for x in contracts if x["action_kernel"]["binding"] == "BOUND"),
            "action_kernel_not_registered": sum(1 for x in contracts if x["action_kernel"]["binding"] == "NOT_REGISTERED"),
            "connector_bindings_exposed": sum(1 for x in contracts if x["connector"].get("binding") == "BOUND"),
            "receipt_contracts_exposed": sum(1 for x in contracts if x["receipt"] != "NOT_EXPOSED"),
            "verification_strategies_exposed": sum(1 for x in contracts if x["verification"] != "NOT_EXPOSED"),
            "reconciliation_strategies_exposed": sum(1 for x in contracts if x["reconciliation"] != "NOT_EXPOSED"),
        },
    }
