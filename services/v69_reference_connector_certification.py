"""V69.7 reference certification for the existing Facebook connector.

This is an offline structural certification. It does not contact Meta and does
not treat possession of a token as proof of provider-side correctness.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.business_integration_platform import ConnectorContract, ConnectionState, IntegrationConnection
from services.connectors.facebook_connector import FacebookConnector
from services.connectors.governed_connector import GovernedConnector
from services.v69_connector_certification import ConnectorCertificationResult, certify_connector
from services.v69_integration_foundation import CredentialBinding, CredentialKind


@dataclass(frozen=True)
class ReferenceCertification:
    result: ConnectorCertificationResult
    connector_key: str = "facebook"
    live_provider_verified: bool = False


class _OfflineFacebookService:
    """Minimal dependency surface: certification must never invoke network I/O."""
    page_id = "offline-page"
    page_token = "opaque-offline-token"


def facebook_contract() -> ConnectorContract:
    return ConnectorContract(
        connector_key="facebook",
        provider="facebook",
        version="1.0.0",
        capabilities=("publish", "lookup", "edit", "delete", "comments"),
        inbound_events=("comment", "message"),
        outbound_actions=("publish", "edit", "delete"),
        authentication_method="oauth2",
        risk_by_action={"publish": "medium", "edit": "medium", "delete": "high"},
        requires_reconciliation=True,
        tenant_scoped=True,
    )


def certify_facebook_reference(*, workspace_id: str = "reference-tenant") -> ReferenceCertification:
    connector = FacebookConnector(_OfflineFacebookService())
    connection = IntegrationConnection(
        workspace_id,
        "facebook",
        ConnectionState.SUGGEST_ENABLED,
        ("publish", "lookup", "edit", "delete", "comments"),
        connection_id="facebook-reference",
    )
    credential = CredentialBinding(
        workspace_id,
        connection.connection_id,
        CredentialKind.OAUTH,
        "vault://kdo/reference/facebook",
        "page:offline",
    )
    # Construct the real governed adapter. No mutation method is called here.
    governed = GovernedConnector(connector, workspace_id=workspace_id, ledger=_NullLedger())
    result = certify_connector(
        connector,
        facebook_contract(),
        connection,
        workspace_id=workspace_id,
        credential=credential,
        governed_adapter=governed,
        rate_limit_contract=True,
        expected_version="1.0.0",
    )
    return ReferenceCertification(result=result)


class _NullLedger:
    """Construction-only ledger sentinel; any accidental execution fails closed."""
    worker_id = "certification"

    def __getattr__(self, name: str) -> Any:
        raise RuntimeError(f"offline certification attempted execution ledger operation: {name}")
