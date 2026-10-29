"""Evidence-backed requirements for unresolved Sector Platform provider gaps.

This is a design/readiness contract, not a provider binding registry.  It makes
an unresolved action explicit enough for a future connector/provider to be
certified without guessing which existing provider is semantically equivalent.
"""
from __future__ import annotations
from typing import Any

GAPS: tuple[dict[str, Any], ...] = (
    {
        "sector_action": "security.contain_asset",
        "status": "NOT_BOUND",
        "required_capability": "security_asset_containment",
        "required_provider_semantics": "provider must isolate/quarantine the addressed asset and expose authoritative containment state",
        "required_receipt": "provider operation/reference receipt identifying the containment request",
        "verification": "authoritative asset state must prove containment, not merely request acceptance",
        "reconciliation": "lookup by provider correlation/reference until terminal containment state is observed; no blind repeat",
        "minimum_binding_gates": ["ASSET_IDENTITY_MAPPING", "CONTAINMENT_SCOPE", "PROVIDER_RECEIPT", "AUTHORITATIVE_CONTAINMENT_VERIFICATION", "SECURITY_RECONCILIATION"],
    },
    {
        "sector_action": "security.remediate_finding",
        "status": "NOT_BOUND",
        "required_capability": "security_finding_remediation",
        "required_provider_semantics": "provider must execute remediation for the addressed finding and expose authoritative remediation state",
        "required_receipt": "provider remediation request/reference receipt",
        "verification": "authoritative finding/control state must prove remediation completion",
        "reconciliation": "lookup remediation state until terminal; ambiguous provider acceptance remains uncertain",
        "minimum_binding_gates": ["FINDING_IDENTITY_MAPPING", "REMEDIATION_SCOPE", "PROVIDER_RECEIPT", "AUTHORITATIVE_REMEDIATION_VERIFICATION", "SECURITY_RECONCILIATION"],
    },
    {
        "sector_action": "supply_chain.dispatch_shipment",
        "status": "NOT_BOUND",
        "required_capability": "shipment_dispatch",
        "required_provider_semantics": "carrier/logistics provider must accept a concrete shipment dispatch request for the governed shipment",
        "required_receipt": "carrier dispatch/consignment reference",
        "verification": "authoritative carrier shipment state must confirm dispatch acceptance/terminal state",
        "reconciliation": "carrier lookup by durable shipment/provider reference; never create a second shipment because acknowledgement is uncertain",
        "minimum_binding_gates": ["SHIPMENT_IDENTITY_MAPPING", "CARRIER_BINDING", "DISPATCH_SCOPE", "PROVIDER_RECEIPT", "CARRIER_STATE_VERIFICATION", "SHIPMENT_RECONCILIATION"],
    },
    {
        "sector_action": "supply_chain.request_carrier_booking",
        "status": "NOT_BOUND",
        "required_capability": "carrier_booking",
        "required_provider_semantics": "carrier provider must create/accept a booking matching the governed shipment and route constraints",
        "required_receipt": "carrier booking reference",
        "verification": "authoritative booking state must identify the requested shipment, carrier and booking status",
        "reconciliation": "lookup booking by deterministic correlation/reference before any retry decision",
        "minimum_binding_gates": ["SHIPMENT_IDENTITY_MAPPING", "CARRIER_BINDING", "BOOKING_CONSTRAINTS", "PROVIDER_RECEIPT", "BOOKING_VERIFICATION", "BOOKING_RECONCILIATION"],
    },
    {
        "sector_action": "manufacturing.release_production",
        "status": "NOT_BOUND",
        "required_capability": "production_release",
        "required_provider_semantics": "manufacturing execution/control provider must release the addressed production order/operation into an authoritative released state",
        "required_receipt": "production-release request/reference",
        "verification": "authoritative manufacturing state must prove release; infrastructure deployment state is insufficient",
        "reconciliation": "query the manufacturing system by durable production identity/reference until terminal release state",
        "minimum_binding_gates": ["PRODUCTION_IDENTITY_MAPPING", "RELEASE_AUTHORIZATION_SCOPE", "MANUFACTURING_PROVIDER_BINDING", "PROVIDER_RECEIPT", "RELEASE_VERIFICATION", "PRODUCTION_RECONCILIATION"],
    },
    {
        "sector_action": "energy.issue_remote_command",
        "status": "NOT_BOUND",
        "required_capability": "utility_asset_remote_command",
        "required_provider_semantics": "utility/OT provider must issue a remote command to the addressed asset and expose authoritative command/asset state",
        "required_receipt": "remote-command correlation/reference receipt",
        "verification": "authoritative asset telemetry/control state must verify command application; request acceptance alone is insufficient",
        "reconciliation": "read authoritative command and asset state; ambiguous command acceptance must not trigger blind reissue",
        "minimum_binding_gates": ["ASSET_IDENTITY_MAPPING", "REMOTE_COMMAND_AUTHORIZATION", "COMMAND_SCOPE", "PROVIDER_RECEIPT", "ASSET_STATE_VERIFICATION", "REMOTE_COMMAND_RECONCILIATION"],
    },
    {
        "sector_action": "agriculture.dispatch_field_task",
        "status": "NOT_BOUND",
        "required_capability": "agricultural_field_task_dispatch",
        "required_provider_semantics": "farm/field operations provider must dispatch the governed field task to an identified field/resource/workforce",
        "required_receipt": "field-task dispatch reference",
        "verification": "authoritative field-operation state must confirm task dispatch/assignment",
        "reconciliation": "lookup task by durable dispatch reference; do not duplicate assignment after uncertain acceptance",
        "minimum_binding_gates": ["FIELD_TASK_IDENTITY_MAPPING", "RESOURCE_SCOPE", "FIELD_PROVIDER_BINDING", "PROVIDER_RECEIPT", "TASK_STATE_VERIFICATION", "FIELD_TASK_RECONCILIATION"],
    },
    {
        "sector_action": "agriculture.request_input_delivery",
        "status": "NOT_BOUND",
        "required_capability": "agricultural_input_delivery",
        "required_provider_semantics": "supplier/logistics provider must create an input-delivery request matching the governed farm, material and quantity",
        "required_receipt": "delivery request/order reference",
        "verification": "authoritative supplier/logistics state must identify the delivery request and its terminal/active status",
        "reconciliation": "lookup by durable delivery reference before retry; no duplicate order on uncertain receipt",
        "minimum_binding_gates": ["FARM_IDENTITY_MAPPING", "INPUT_SPECIFICATION", "SUPPLIER_OR_LOGISTICS_BINDING", "PROVIDER_RECEIPT", "DELIVERY_VERIFICATION", "INPUT_DELIVERY_RECONCILIATION"],
    },
)


def build_sector_provider_gap_requirements() -> dict[str, Any]:
    return {
        "schema": "KDO.SectorProviderGapRequirements/v1",
        "read_only": True,
        "authority": {
            "this_surface_grants_execution": False,
            "this_surface_selects_a_provider": False,
            "action_kernel_remains_execution_authority": True,
            "connector_fabric_remains_provider_execution_authority": True,
        },
        "summary": {"unbound_actions": len(GAPS)},
        "gaps": [dict(item) for item in GAPS],
    }
