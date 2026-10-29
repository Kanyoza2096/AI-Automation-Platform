"""Canonical sector-to-provider binding specifications.

This module records the *required* semantic mapping between a Sector Pack action
and an existing governed provider action.  It is deliberately not an execution
registry: a binding cannot become executable until every declared mapping field
is satisfied and the Action Kernel/Connector Fabric remain the authorities.
"""
from __future__ import annotations
from typing import Any

BINDINGS: tuple[dict[str, Any], ...] = (
    {"sector_action":"it.remediate_service","governed_provider_action":"github.dispatch_workflow","provider":"github","status":"BOUND","parameter_mapping":{"service_id":"workspace-scoped IT service -> active GitHub service-provider binding","reason":"governed remediation reason -> GitHub workflow input","workflow":"binding metadata owner/repo/workflow_id/ref"},"outcome_mapping":{"provider_receipt":"GitHub workflow dispatch receipt","terminal_state":"GitHub workflow run status/conclusion lookup","sector_outcome":"IT remediation recorded only after terminal workflow reconciliation"},"required_gates":("SERVICE_IDENTITY_MAPPING","GITHUB_REMEDIATION_BINDING","WORKFLOW_DISPATCH_SCOPE","TERMINAL_WORKFLOW_VERIFICATION","IT_REMEDIATION_RECONCILIATION"),"satisfied_gates":("SERVICE_IDENTITY_MAPPING","GITHUB_REMEDIATION_BINDING","WORKFLOW_DISPATCH_SCOPE","TERMINAL_WORKFLOW_VERIFICATION","IT_REMEDIATION_RECONCILIATION","DURABLE_IT_REMEDIATION_RECONCILIATION_EVIDENCE"),"authority":"ACTION_KERNEL_THEN_CONNECTOR_FABRIC"},
    {"sector_action":"commerce.fulfill_order","governed_provider_action":"shopify.create_fulfillment","provider":"shopify","status":"BOUND","parameter_mapping":{"order_id":"workspace-scoped commerce order -> exactly one open/scheduled Shopify FulfillmentOrder","notify_customer":"governed notification flag -> Shopify fulfillment notification preference","message":"optional fulfillment message -> Shopify mutation message"},"outcome_mapping":{"provider_receipt":"Shopify fulfillment receipt","terminal_state":"Shopify Fulfillment.status lookup","sector_outcome":"commerce fulfillment recorded only after terminal reconciliation"},"required_gates":("ORDER_IDENTITY_MAPPING","SINGLE_FULFILLMENT_ORDER_POLICY","SHOPIFY_FULFILLMENT_CREATE_SCOPE","TERMINAL_FULFILLMENT_VERIFICATION","COMMERCE_FULFILLMENT_RECONCILIATION"),"satisfied_gates":("ORDER_IDENTITY_MAPPING","SINGLE_FULFILLMENT_ORDER_POLICY","SHOPIFY_FULFILLMENT_CREATE_SCOPE","TERMINAL_FULFILLMENT_VERIFICATION","COMMERCE_FULFILLMENT_RECONCILIATION","DURABLE_COMMERCE_FULFILLMENT_RECONCILIATION_EVIDENCE"),"authority":"ACTION_KERNEL_THEN_CONNECTOR_FABRIC"},
    {"sector_action":"commerce.capture_payment","governed_provider_action":"stripe.capture_payment_intent","provider":"stripe","status":"BOUND","parameter_mapping":{"order_id":"workspace-scoped order -> existing payment record","amount":"capture amount must not exceed order total/payment amount","currency":"must match existing payment currency"},"outcome_mapping":{"provider_receipt":"Stripe capture receipt","terminal_state":"Stripe PaymentIntent status lookup","sector_outcome":"commerce payment captured only after terminal reconciliation"},"required_gates":("ORDER_PAYMENT_IDENTITY_MAPPING","PROVIDER_PAYMENT_REFERENCE_MAPPING","CAPTURE_AMOUNT_POLICY","TERMINAL_PAYMENT_VERIFICATION","COMMERCE_PAYMENT_RECONCILIATION"),"satisfied_gates":("ORDER_PAYMENT_IDENTITY_MAPPING","PROVIDER_PAYMENT_REFERENCE_MAPPING","CAPTURE_AMOUNT_POLICY","TERMINAL_PAYMENT_VERIFICATION","COMMERCE_PAYMENT_RECONCILIATION","DURABLE_COMMERCE_PAYMENT_RECONCILIATION_EVIDENCE"),"authority":"ACTION_KERNEL_THEN_CONNECTOR_FABRIC"},
    {
        "sector_action": "commerce.send_order_notification",
        "governed_provider_action": "twilio.send_sms",
        "provider": "twilio", "status": "BOUND",
        "parameter_mapping": {"order_id": "workspace-scoped commerce order -> customer phone", "message": "notification body -> Twilio Body", "from_": "approved Twilio sender identity", "reference": "workspace-scoped deterministic order-notification fence"},
        "outcome_mapping": {"provider_receipt": "Twilio message receipt", "terminal_state": "Twilio message status lookup", "sector_outcome": "commerce order-notification delivery outcome after reconciliation"},
        "required_gates": ("ORDER_CUSTOMER_IDENTITY_MAPPING","SENDER_IDENTITY_MAPPING","IDEMPOTENCY_ORDER_NOTIFICATION_FENCE","DELIVERY_STATUS_VERIFICATION","COMMERCE_NOTIFICATION_RECONCILIATION"),
        "satisfied_gates": ("ORDER_CUSTOMER_IDENTITY_MAPPING","SENDER_IDENTITY_MAPPING","IDEMPOTENCY_ORDER_NOTIFICATION_FENCE","DELIVERY_STATUS_VERIFICATION","COMMERCE_NOTIFICATION_RECONCILIATION","DURABLE_ORDER_NOTIFICATION_RECONCILIATION_EVIDENCE"),
        "authority": "ACTION_KERNEL_THEN_CONNECTOR_FABRIC",
    },

    {
        "sector_action": "healthcare.send_patient_message",
        "governed_provider_action": "twilio.send_sms",
        "provider": "twilio", "status": "BOUND",
        "parameter_mapping": {"patient_id": "workspace-scoped Healthcare patient -> patient phone", "message": "patient message body -> Twilio Body", "from_": "approved Twilio sender identity", "reference": "workspace-scoped deterministic patient-message fence"},
        "outcome_mapping": {"provider_receipt": "Twilio message receipt", "terminal_state": "Twilio message status lookup", "sector_outcome": "healthcare patient-message delivery outcome after reconciliation"},
        "required_gates": ("PATIENT_IDENTITY_MAPPING","SENDER_IDENTITY_MAPPING","IDEMPOTENCY_PATIENT_MESSAGE_FENCE","DELIVERY_STATUS_VERIFICATION","HEALTHCARE_MESSAGE_RECONCILIATION"),
        "satisfied_gates": ("PATIENT_IDENTITY_MAPPING","SENDER_IDENTITY_MAPPING","IDEMPOTENCY_PATIENT_MESSAGE_FENCE","DELIVERY_STATUS_VERIFICATION","HEALTHCARE_MESSAGE_RECONCILIATION","DURABLE_PATIENT_MESSAGE_RECONCILIATION_EVIDENCE"),
        "authority": "ACTION_KERNEL_THEN_CONNECTOR_FABRIC",
    },
    {
        "sector_action": "education.send_parent_message",
        "governed_provider_action": "twilio.send_sms",
        "provider": "twilio", "status": "BOUND",
        "parameter_mapping": {"student_id": "workspace-scoped Education student -> guardian phone", "message": "parent message body -> Twilio Body", "from_": "approved Twilio sender identity", "reference": "workspace-scoped deterministic parent-message fence"},
        "outcome_mapping": {"provider_receipt": "Twilio message receipt", "terminal_state": "Twilio message status lookup", "sector_outcome": "education parent-message delivery outcome after reconciliation"},
        "required_gates": ("STUDENT_GUARDIAN_IDENTITY_MAPPING","SENDER_IDENTITY_MAPPING","IDEMPOTENCY_PARENT_MESSAGE_FENCE","DELIVERY_STATUS_VERIFICATION","EDUCATION_MESSAGE_RECONCILIATION"),
        "satisfied_gates": ("STUDENT_GUARDIAN_IDENTITY_MAPPING","SENDER_IDENTITY_MAPPING","IDEMPOTENCY_PARENT_MESSAGE_FENCE","DELIVERY_STATUS_VERIFICATION","EDUCATION_MESSAGE_RECONCILIATION","DURABLE_PARENT_MESSAGE_RECONCILIATION_EVIDENCE"),
        "authority": "ACTION_KERNEL_THEN_CONNECTOR_FABRIC",
    },
    {
        "sector_action": "crm.send_message",
        "governed_provider_action": "twilio.send_sms",
        "provider": "twilio",
        "status": "BOUND",
        "parameter_mapping": {
            "message": "CRM message body -> Twilio Body",
            "status": "workspace-scoped CRM contact status -> recipient set",
            "from_": "approved Twilio sender identity",
            "reference": "workspace-scoped deterministic delivery fence",
        },
        "outcome_mapping": {
            "provider_receipt": "Twilio message receipt",
            "terminal_state": "Twilio message status lookup",
            "sector_outcome": "CRM message delivery outcome after reconciliation",
        },
        "required_gates": (
            "RECIPIENT_SCOPE_MAPPING",
            "SENDER_IDENTITY_MAPPING",
            "IDEMPOTENCY_DELIVERY_FENCE",
            "DELIVERY_STATUS_VERIFICATION",
            "CRM_MESSAGE_RECONCILIATION",
        ),
        "satisfied_gates": (
            "RECIPIENT_SCOPE_MAPPING",
            "SENDER_IDENTITY_MAPPING",
            "IDEMPOTENCY_DELIVERY_FENCE",
            "DELIVERY_STATUS_VERIFICATION",
            "CRM_MESSAGE_RECONCILIATION",
            "DURABLE_MESSAGE_RECONCILIATION_EVIDENCE",
        ),
        "authority": "ACTION_KERNEL_THEN_CONNECTOR_FABRIC",
    },
    {
        "sector_action": "it.request_deployment",
        "governed_provider_action": "render.trigger_deploy",
        "provider": "render",
        "status": "BOUND",
        "parameter_mapping": {
            "service_id": "active Render service-provider binding",
            "version": "active Render deployment-version reference -> provider commit",
            "environment": "IT service environment + Render binding environment validation",
        },
        "outcome_mapping": {
            "provider_receipt": "Render deploy receipt",
            "terminal_state": "RenderAdapter.reconcile_deploy",
            "sector_outcome": "it.record_deployment_outcome",
        },
        "required_gates": (
            "SERVICE_IDENTITY_MAPPING",
            "VERSION_TO_PROVIDER_REFERENCE_MAPPING",
            "ENVIRONMENT_POLICY_MAPPING",
            "SECTOR_OUTCOME_PERSISTENCE_BINDING",
        ),
        "satisfied_gates": (
            "SERVICE_IDENTITY_MAPPING",
            "VERSION_TO_PROVIDER_REFERENCE_MAPPING",
            "ENVIRONMENT_POLICY_MAPPING",
            "SECTOR_OUTCOME_PERSISTENCE_BINDING",
            "POST_DEPLOYMENT_RECONCILIATION",
            "DURABLE_RECONCILIATION_EVIDENCE",
            "TERMINAL_VERIFICATION",
        ),
        "authority": "ACTION_KERNEL_THEN_CONNECTOR_FABRIC",
    },
    {
        "sector_action": "manufacturing.request_maintenance",
        "governed_provider_action": "jira.create_issue",
        "provider": "jira",
        "status": "BOUND",
        "parameter_mapping": {"maintenance_id":"workspace-scoped maintenance work order","reason":"governed maintenance reason -> Jira description","project_key":"active workspace Jira maintenance binding","issue_type":"active workspace Jira maintenance binding"},
        "outcome_mapping": {"provider_receipt":"Jira issue receipt","terminal_state":"Jira issue lookup confirms provider object","sector_outcome":"maintenance request recorded only after authoritative Jira verification"},
        "required_gates": ("MAINTENANCE_IDENTITY_MAPPING","JIRA_MAINTENANCE_BINDING","ISSUE_CREATE_SCOPE","TERMINAL_ISSUE_VERIFICATION","MANUFACTURING_MAINTENANCE_RECONCILIATION"),
        "satisfied_gates": ("MAINTENANCE_IDENTITY_MAPPING","JIRA_MAINTENANCE_BINDING","ISSUE_CREATE_SCOPE","TERMINAL_ISSUE_VERIFICATION","MANUFACTURING_MAINTENANCE_RECONCILIATION","DURABLE_MAINTENANCE_RECONCILIATION_EVIDENCE"),
        "authority": "ACTION_KERNEL_THEN_CONNECTOR_FABRIC",
    },
    {
        "sector_action": "government.dispatch_field_service",
        "governed_provider_action": "jira.create_issue",
        "provider": "jira",
        "status": "BOUND",
        "parameter_mapping": {"request_id": "workspace-scoped government service request", "team_id": "governed field-service team reference", "project_key": "active workspace Jira field-service binding", "issue_type": "active workspace Jira field-service binding"},
        "outcome_mapping": {"provider_receipt": "Jira issue receipt", "terminal_state": "Jira issue lookup confirms provider object", "sector_outcome": "field-service dispatch recorded only after authoritative Jira verification"},
        "required_gates": ("SERVICE_REQUEST_IDENTITY_MAPPING", "JIRA_FIELD_SERVICE_BINDING", "ISSUE_CREATE_SCOPE", "TERMINAL_ISSUE_VERIFICATION", "GOVERNMENT_FIELD_SERVICE_RECONCILIATION"),
        "satisfied_gates": ("SERVICE_REQUEST_IDENTITY_MAPPING", "JIRA_FIELD_SERVICE_BINDING", "ISSUE_CREATE_SCOPE", "TERMINAL_ISSUE_VERIFICATION", "GOVERNMENT_FIELD_SERVICE_RECONCILIATION", "DURABLE_FIELD_SERVICE_RECONCILIATION_EVIDENCE"),
        "authority": "ACTION_KERNEL_THEN_CONNECTOR_FABRIC",
    },
    {
        "sector_action": "energy.dispatch_crew",
        "governed_provider_action": "jira.create_issue",
        "provider": "jira",
        "status": "BOUND",
        "parameter_mapping": {"work_order_id":"workspace-scoped energy utility work order", "crew_id":"governed crew reference", "project_key":"active workspace Jira crew-dispatch binding", "issue_type":"active workspace Jira crew-dispatch binding"},
        "outcome_mapping": {"provider_receipt":"Jira issue receipt", "terminal_state":"Jira issue lookup confirms provider dispatch request", "sector_outcome":"crew-dispatch request recorded only after authoritative Jira verification"},
        "required_gates": ("WORK_ORDER_IDENTITY_MAPPING", "JIRA_CREW_DISPATCH_BINDING", "ISSUE_CREATE_SCOPE", "TERMINAL_ISSUE_VERIFICATION", "ENERGY_CREW_DISPATCH_RECONCILIATION"),
        "satisfied_gates": ("WORK_ORDER_IDENTITY_MAPPING", "JIRA_CREW_DISPATCH_BINDING", "ISSUE_CREATE_SCOPE", "TERMINAL_ISSUE_VERIFICATION", "ENERGY_CREW_DISPATCH_RECONCILIATION", "DURABLE_CREW_DISPATCH_RECONCILIATION_EVIDENCE"),
        "authority": "ACTION_KERNEL_THEN_CONNECTOR_FABRIC",
    },
    {
        "sector_action": "finance.send_statement",
        "governed_provider_action": "twilio.send_sms",
        "provider": "twilio", "status": "BOUND",
        "parameter_mapping": {"account_id":"workspace-scoped finance account","to_number":"approved statement destination","message":"statement delivery body -> Twilio Body","from_":"approved Twilio sender identity","reference":"workspace-scoped deterministic statement-delivery fence"},
        "outcome_mapping": {"provider_receipt":"Twilio message receipt","terminal_state":"Twilio message status lookup","sector_outcome":"statement delivery recorded only after authoritative provider verification"},
        "required_gates": ("ACCOUNT_IDENTITY_MAPPING","STATEMENT_DESTINATION_MAPPING","SENDER_IDENTITY_MAPPING","IDEMPOTENCY_STATEMENT_DELIVERY_FENCE","DELIVERY_STATUS_VERIFICATION","FINANCE_STATEMENT_RECONCILIATION"),
        "satisfied_gates": ("ACCOUNT_IDENTITY_MAPPING","STATEMENT_DESTINATION_MAPPING","SENDER_IDENTITY_MAPPING","IDEMPOTENCY_STATEMENT_DELIVERY_FENCE","DELIVERY_STATUS_VERIFICATION","FINANCE_STATEMENT_RECONCILIATION","DURABLE_STATEMENT_RECONCILIATION_EVIDENCE"),
        "authority": "ACTION_KERNEL_THEN_CONNECTOR_FABRIC",
    },
    {
        "sector_action": "finance.initiate_payment",
        "governed_provider_action": "stripe.create_payment_intent",
        "provider": "stripe",
        "status": "BOUND",
        "parameter_mapping": {
            "amount": "sector amount -> Stripe minor currency unit",
            "currency": "direct ISO-4217 code",
            "reference": "workspace-scoped deterministic KDO idempotency key",
        },
        "outcome_mapping": {
            "provider_receipt": "Stripe payment-intent receipt",
            "terminal_state": "REQUIRES_PAYMENT_LIFECYCLE_VERIFICATION",
            "sector_outcome": "REQUIRES_FINANCE_OUTCOME_CONTRACT",
        },
        "required_gates": (
            "PAYMENT_LIFECYCLE_SEMANTICS",
            "IDEMPOTENCY_REFERENCE_POLICY",
            "TERMINAL_PAYMENT_VERIFICATION",
            "FINANCE_OUTCOME_RECONCILIATION",
        ),
        "satisfied_gates": (
            "PAYMENT_LIFECYCLE_SEMANTICS",
            "IDEMPOTENCY_REFERENCE_POLICY",
            "TERMINAL_PAYMENT_VERIFICATION",
            "FINANCE_OUTCOME_RECONCILIATION",
            "DURABLE_PAYMENT_RECONCILIATION_EVIDENCE",
        ),
        "authority": "ACTION_KERNEL_THEN_CONNECTOR_FABRIC",
    },
)


def build_sector_provider_binding_contracts() -> dict[str, Any]:
    """Return a read-only, explicit binding specification."""
    return {
        "schema": "KDO.SectorProviderBindingContracts/v1",
        "read_only": True,
        "authority": {
            "sector_pack_grants_execution": False,
            "action_kernel_is_execution_authority": True,
            "connector_fabric_is_provider_authority": True,
        },
        "count": len(BINDINGS),
        "bindings": [dict(item) for item in BINDINGS],
    }
