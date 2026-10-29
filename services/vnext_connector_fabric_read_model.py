"""Canonical Connector Fabric read model for the vNext control plane.

This module exposes connector architecture as backend-owned evidence. It does
not instantiate adapters, read credentials, probe providers, mutate registry
state, or grant execution authority.

Roadmap shape:
    Provider -> Capability -> Action Contract -> Provider Adapter
    -> Health -> Certification -> Receipt -> Verification -> Reconciliation

Only facts that are currently represented by an authoritative code contract
are exposed. Live health/certification state is explicitly NOT_EXPOSED here
until a durable operator read contract exists.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import importlib

from services.connectors.enterprise_registry import supported_enterprise_providers
from services.connectors.connector_factory import supported_platforms
from services.connectors.ai_registry import supported_ai_providers



NOT_EXPOSED = "NOT_EXPOSED"


@dataclass(frozen=True)
class ActionContractView:
    action_type: str
    risk_level: str
    required_parameters: tuple[str, ...]
    requires_approval: bool
    irreversible: bool
    reconciliation_strategy: str
    verification_strategy: str = NOT_EXPOSED


    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action_type,
            "risk": self.risk_level,
            "requiredParameters": list(self.required_parameters),
            "requiresApproval": self.requires_approval,
            "irreversible": self.irreversible,
            "reconciliation": self.reconciliation_strategy,
            "verification": self.verification_strategy,
        }


@dataclass(frozen=True)
class ConnectorFabricRecord:
    connector_key: str
    provider: str
    adapter_module: str
    adapter_class: str
    connector_version: str
    capabilities: tuple[str, ...]
    action_contracts: tuple[ActionContractView, ...]
    receipt_protocol: str
    verification_strategy: str
    health: str
    certification: str
    authority: str = "none"

    def to_dict(self) -> dict[str, Any]:
        return {
            "connectorKey": self.connector_key,
            "provider": self.provider,
            "providerAdapter": {
                "module": self.adapter_module,
                "class": self.adapter_class,
                "version": self.connector_version,
            },
            "capabilities": list(self.capabilities),
            "actionContracts": [a.to_dict() for a in self.action_contracts],
            "receipt": {"protocol": self.receipt_protocol},
            "verification": self.verification_strategy,
            "health": self.health,
            "certification": self.certification,
            "authority": self.authority,
        }


def _canonical_action_contracts(provider: str) -> tuple[ActionContractView, ...]:
    """Read the Action Kernel's actual registered contracts without adapters.

    Registration helpers only construct ActionContract objects and register them;
    no provider call or credential lookup occurs here. This keeps the Connector
    Fabric projection from maintaining a second, drifting action registry.
    """
    from services.connectors import enterprise_action_contracts as contracts

    class _NullAdapter:
        def __getattr__(self, _name: str):
            return lambda *args, **kwargs: None

    class _Kernel:
        def __init__(self):
            self.actions = []

        def register(self, action):
            self.actions.append(action)

    kernel = _Kernel()
    adapter = _NullAdapter()
    if provider == "github":
        contracts.register_github_actions(kernel, adapter)
    elif provider == "render":
        contracts.register_render_actions(kernel, adapter)
    elif provider == "google_workspace":
        contracts.register_google_workspace_actions(kernel, adapter)
    else:
        contracts.register_modern_actions(kernel, {provider: adapter})

    adapter_methods: set[str] = set()
    adapter_spec = {
        "github": ("services.connectors.github_adapter", "GitHubAdapter"),
        "render": ("services.connectors.render_adapter", "RenderAdapter"),
        "google_workspace": ("services.connectors.google_workspace_adapter", "GoogleWorkspaceAdapter"),
        "stripe": ("services.connectors.modern_adapters", "StripeAdapter"),
        "shopify": ("services.connectors.modern_adapters", "ShopifyAdapter"),
        "slack": ("services.connectors.modern_adapters", "SlackAdapter"),
        "jira": ("services.connectors.modern_adapters", "JiraAdapter"),
        "pagerduty": ("services.connectors.modern_adapters", "PagerDutyAdapter"),
        "datadog": ("services.connectors.modern_adapters", "DatadogAdapter"),
        "cloudflare": ("services.connectors.modern_adapters", "CloudflareAdapter"),
        "twilio": ("services.connectors.modern_adapters", "TwilioAdapter"),
        "aws": ("services.connectors.aws_adapter", "AWSAdapter"),
    }
    spec = adapter_spec.get(provider)
    if spec:
        module = importlib.import_module(spec[0])
        adapter_cls = getattr(module, spec[1])
        adapter_methods = {name for name in dir(adapter_cls) if name.startswith("reconcile_")}

    def reconciliation_for(action_type: str) -> str:
        action = action_type.rsplit(".", 1)[-1]
        if provider == "github" and action in {"dispatch_workflow", "rerun_workflow", "cancel_workflow_run"}:
            return "reconcile_workflow" if "reconcile_workflow" in adapter_methods else NOT_EXPOSED
        if provider == "render" and action in {"trigger_deploy", "cancel_deploy"}:
            return "reconcile_deploy" if "reconcile_deploy" in adapter_methods else NOT_EXPOSED
        method = f"reconcile_{action}"
        return method if method in adapter_methods else NOT_EXPOSED

    def verification_for(action_type: str) -> str:
        """Expose only implemented authoritative/read-side evidence paths.

        This is deliberately conservative: a provider having a generic GET
        method does not prove that it is a complete reconciliation strategy.
        The value therefore describes an evidence mechanism, not success.
        """
        action = action_type.rsplit(".", 1)[-1]
        if provider == "github":
            if action in {"dispatch_workflow", "rerun_workflow", "cancel_workflow_run"} and "reconcile_workflow" in adapter_methods:
                return "reconcile_workflow"
            if action in {"create_issue", "add_issue_comment", "merge_pull_request"}:
                return "provider_api_lookup:implemented"
        if provider == "render":
            if action in {"trigger_deploy", "cancel_deploy"} and "reconcile_deploy" in adapter_methods:
                return "reconcile_deploy"
        lookup_by_provider = {
            "stripe": "get_payment_intent",
            "shopify": "get_order",
            "slack": "list_channels",
            "jira": "get_issue",
            "pagerduty": "get_incident",
            "datadog": "get_monitors",
            "cloudflare": "get_zone",
            "twilio": "get_message",
            "google_workspace": "get_drive_file/search_drive_files",
            "aws": "describe_service",
        }
        method = lookup_by_provider.get(provider)
        return f"provider_api_lookup:{method}" if method else NOT_EXPOSED

    return tuple(
        ActionContractView(
            action.action_type,
            action.risk_level,
            tuple(action.required_parameters),
            action.requires_approval,
            action.irreversible,
            reconciliation_for(action.action_type),
            verification_for(action.action_type),
        )
        for action in kernel.actions
    )


def _enterprise_record(
    connector_key: str,
    provider: str,
    adapter_module: str,
    adapter_class: str,
    capabilities: tuple[str, ...],
) -> ConnectorFabricRecord:
    return ConnectorFabricRecord(
        connector_key,
        provider,
        adapter_module,
        adapter_class,
        NOT_EXPOSED,
        capabilities,
        _canonical_action_contracts(provider),
        "ProviderReceipt",
        NOT_EXPOSED,
        NOT_EXPOSED,
        NOT_EXPOSED,
    )


# These are the providers explicitly registered by the existing enterprise
# connector registry. The Action Contracts are derived from the canonical
# Action Kernel registration helpers above; no duplicate contract facts live in
# this read model.
_ENTERPRISE: tuple[ConnectorFabricRecord, ...] = (
    _enterprise_record("github", "github", "services.connectors.github_adapter", "GitHubAdapter",
                       ("issues", "issue_comments", "workflow_dispatch", "workflow_rerun", "workflow_cancel", "pull_request_merge")),
    _enterprise_record("render", "render", "services.connectors.render_adapter", "RenderAdapter",
                       ("service_read", "deploy_read", "deploy_trigger", "deploy_cancel")),
    _enterprise_record("stripe", "stripe", "services.connectors.modern_adapters", "StripeAdapter", ("payment_intent_create",)),
    _enterprise_record("shopify", "shopify", "services.connectors.modern_adapters", "ShopifyAdapter", ("order_cancel",)),
    _enterprise_record("slack", "slack", "services.connectors.modern_adapters", "SlackAdapter", ("message_post",)),
    _enterprise_record("jira", "jira", "services.connectors.modern_adapters", "JiraAdapter", ("issue_create",)),
    _enterprise_record("pagerduty", "pagerduty", "services.connectors.modern_adapters", "PagerDutyAdapter", ("incident_create",)),
    _enterprise_record("datadog", "datadog", "services.connectors.modern_adapters", "DatadogAdapter", ("downtime_create",)),
    _enterprise_record("cloudflare", "cloudflare", "services.connectors.modern_adapters", "CloudflareAdapter", ("cache_purge",)),
    _enterprise_record("twilio", "twilio", "services.connectors.modern_adapters", "TwilioAdapter", ("sms_send",)),
    _enterprise_record("google_workspace", "google_workspace", "services.connectors.google_workspace_adapter", "GoogleWorkspaceAdapter",
                       ("gmail_send", "calendar_event_create", "sheet_append", "document_create")),
    _enterprise_record("aws", "aws", "services.connectors.aws_adapter", "AWSAdapter", ("ecs_service_update", "object_put")),
)


_SOCIAL: tuple[ConnectorFabricRecord, ...] = tuple(
    ConnectorFabricRecord(key, key, f"services.connectors.{key}_connector", cls, NOT_EXPOSED,
        caps, (), "ProviderReceipt", "authoritative_lookup:lookup_post", NOT_EXPOSED, NOT_EXPOSED)
    for key, cls, caps in (
        ("facebook", "FacebookConnector", ("publish", "lookup", "edit", "delete", "comments", "messages")),
        ("twitter", "TwitterConnector", ("publish", "lookup", "edit", "delete", "comments", "messages")),
        ("linkedin", "LinkedInConnector", ("publish", "lookup", "edit", "delete", "comments", "messages")),
        ("instagram", "InstagramConnector", ("publish", "lookup", "edit", "delete", "comments", "messages")),
        ("telegram", "TelegramConnector", ("publish", "edit", "delete", "comments", "messages")),
        ("whatsapp", "WhatsAppConnector", ("publish", "lookup", "edit", "delete", "comments", "messages")),
        ("website", "WebsiteConnector", ("publish", "lookup", "edit", "delete", "comments", "messages")),
    )
)


def connector_fabric_catalog() -> tuple[ConnectorFabricRecord, ...]:
    return _ENTERPRISE + _SOCIAL


def connector_fabric_registry_integrity() -> dict[str, Any]:
    """Return registry coverage without instantiating adapters or providers."""
    catalog_keys = {record.connector_key for record in connector_fabric_catalog()}
    enterprise = tuple(supported_enterprise_providers())
    social = tuple(supported_platforms())
    ai = tuple(supported_ai_providers())
    registered = set(enterprise) | set(social)
    return {
        "enterpriseProviders": list(enterprise),
        "socialConnectors": list(social),
        "aiProvidersSeparateBoundary": list(ai),
        "catalogConnectorCount": len(catalog_keys),
        "registeredConnectorCount": len(registered),
        "missingFromCatalog": sorted(registered - catalog_keys),
        "unexpectedInCatalog": sorted(catalog_keys - registered),
        "aiExcludedFromConnectorCount": True,
    }


def build_connector_fabric_read_model(workspace_id: str) -> dict[str, Any]:
    if not workspace_id or not workspace_id.strip():
        raise ValueError("workspace_id is required")
    records = connector_fabric_catalog()
    return {
        "workspaceId": workspace_id,
        "readOnly": True,
        "authority": "none",
        "architecture": "Provider -> Capability -> Action Contract -> Provider Adapter -> Health -> Certification -> Receipt -> Verification -> Reconciliation",
        "healthSource": "NOT_EXPOSED",
        "certificationSource": "NOT_EXPOSED",
        "connectors": [r.to_dict() for r in records],
        "counts": {
            "providers": len({r.provider for r in records}),
            "connectors": len(records),
            "actionContracts": sum(len(r.action_contracts) for r in records),
        },
        "registryIntegrity": connector_fabric_registry_integrity(),
    }


def get_connector_fabric_record(workspace_id: str, connector_key: str) -> dict[str, Any]:
    if not connector_key.strip():
        raise ValueError("connector_key is required")
    for record in connector_fabric_catalog():
        if record.connector_key == connector_key:
            payload = record.to_dict()
            payload["workspaceId"] = workspace_id
            payload["readOnly"] = True
            return payload
    raise KeyError(connector_key)



def connector_outcome_evidence_coverage() -> dict[str, Any]:
    """Return a conservative portfolio-level receipt/verification/reconciliation matrix.

    This is architecture evidence only. It never probes providers and never
    treats a verification path as proof that an external operation succeeded.
    """
    records = connector_fabric_catalog()
    rows = []
    for record in records:
        actions = list(record.action_contracts)
        reconciled = [a.action_type for a in actions if a.reconciliation_strategy != NOT_EXPOSED]
        verified = [a.action_type for a in actions if a.verification_strategy != NOT_EXPOSED]
        rows.append({
            "connectorKey": record.connector_key,
            "receiptProtocol": record.receipt_protocol,
            "receiptTranslation": "canonical:ProviderReceipt",
            "verification": {
                "status": "IMPLEMENTED" if verified else NOT_EXPOSED,
                "actions": verified,
            },
            "reconciliation": {
                "status": "IMPLEMENTED" if reconciled else NOT_EXPOSED,
                "actions": reconciled,
            },
            "authority": "none",
        })
    return {
        "readOnly": True,
        "authority": "none",
        "connectors": rows,
        "counts": {
            "connectors": len(rows),
            "receiptProtocolImplemented": sum(r["receiptProtocol"] == "ProviderReceipt" for r in rows),
            "verificationImplemented": sum(r["verification"]["status"] == "IMPLEMENTED" for r in rows),
            "reconciliationImplemented": sum(r["reconciliation"]["status"] == "IMPLEMENTED" for r in rows),
        },
        "note": "Implemented means a code-backed evidence path exists; it does not assert live provider success.",
    }

__all__ = ["build_connector_fabric_read_model", "get_connector_fabric_record", "connector_fabric_catalog", "connector_fabric_registry_integrity", "connector_outcome_evidence_coverage", "NOT_EXPOSED"]
