"""Authenticated, workspace-scoped Facebook health context resolution.

This read-only adapter hydrates the integration connection and certification
registry from durable rows, then independently resolves the Page account.
Callers never submit preconstructed connection or registry objects.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from services.business_integration_platform import (
    AutonomyMode,
    ConnectionState,
    ConnectorContract,
    IntegrationConnection,
)
from services.v69_connector_activation_registry import (
    CertificationEvidence,
    ConnectorRegistryRecord,
    RegistryState,
)
from services.v69_connector_certification import CertificationStatus
from services.v69_tenant_social_account_resolver import (
    TenantSocialAccountBinding,
    TenantSocialAccountResolver,
)


class TenantFacebookHealthContextError(ValueError):
    """A tenant's connector health context could not be resolved safely."""


@dataclass(frozen=True)
class TenantFacebookHealthContext:
    workspace_id: str
    binding: TenantSocialAccountBinding
    connection: IntegrationConnection
    contract: ConnectorContract
    registry: ConnectorRegistryRecord


def _parse_datetime(value: Any, field_name: str, *, optional: bool = False):
    if value is None and optional:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise TenantFacebookHealthContextError(f"{field_name}_invalid") from exc
    else:
        raise TenantFacebookHealthContextError(f"{field_name}_missing")
    if parsed.tzinfo is None:
        raise TenantFacebookHealthContextError(f"{field_name}_timezone_required")
    return parsed


def _one_row(client: Any, table: str, *, workspace_id: str, filters: Mapping[str, str]):
    try:
        query = client.table(table).select("*").eq("workspace_id", workspace_id)
        for key, value in filters.items():
            query = query.eq(key, value)
        response = query.limit(2).execute()
    except Exception as exc:
        raise TenantFacebookHealthContextError(f"{table}_lookup_failed") from exc
    rows = getattr(response, "data", None) or []
    if not rows:
        raise TenantFacebookHealthContextError(f"{table}_not_found")
    if len(rows) != 1:
        raise TenantFacebookHealthContextError(f"{table}_ambiguous")
    if not isinstance(rows[0], Mapping):
        raise TenantFacebookHealthContextError(f"{table}_row_invalid")
    return rows[0]


class TenantFacebookHealthContextResolver:
    """Resolve only authenticated workspace-owned Facebook connection state."""

    _ALLOWED_CONNECTION_STATES = {
        ConnectionState.OBSERVE_ONLY,
        ConnectionState.SUGGEST_ENABLED,
        ConnectionState.EXECUTION_ENABLED,
        ConnectionState.DEGRADED,
    }
    _ALLOWED_REGISTRY_STATES = {
        RegistryState.ACTIVE,
        RegistryState.DEGRADED,
    }

    def __init__(
        self,
        supabase_client: Any,
        *,
        social_account_resolver: TenantSocialAccountResolver | None = None,
    ) -> None:
        if supabase_client is None:
            raise TenantFacebookHealthContextError("explicit_supabase_client_required")
        self._client = supabase_client
        self._accounts = social_account_resolver or TenantSocialAccountResolver(supabase_client)

    def resolve_for_identity(
        self,
        identity: Any,
        *,
        brand_id: str,
        connection_id: str,
        now: datetime | None = None,
    ) -> TenantFacebookHealthContext:
        if not identity or not getattr(identity, "valid", False):
            raise TenantFacebookHealthContextError("authenticated_identity_required")
        workspace_id = str(getattr(identity, "workspace_id", "") or "").strip()
        if not workspace_id:
            raise TenantFacebookHealthContextError("authenticated_workspace_required")
        if not isinstance(connection_id, str) or not connection_id.strip():
            raise TenantFacebookHealthContextError("connection_id_required")
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise TenantFacebookHealthContextError("now_timezone_required")

        try:
            binding = self._accounts.resolve_for_identity(
                identity,
                brand_id=brand_id,
                platform="facebook",
            )
        except Exception as exc:
            raise TenantFacebookHealthContextError("tenant_facebook_account_resolution_failed") from exc
        if binding.workspace_id != workspace_id or binding.brand_id != brand_id:
            raise TenantFacebookHealthContextError("resolved_page_tenant_binding_mismatch")

        connection_row = _one_row(
            self._client,
            "integration_connections",
            workspace_id=workspace_id,
            filters={"id": connection_id.strip(), "connector_key": "facebook"},
        )
        stored_brand_id = str(connection_row.get("brand_id") or "")
        stored_account_id = str(connection_row.get("social_account_id") or "")
        if (
            stored_brand_id != binding.brand_id
            or stored_account_id != binding.account_id
            or str(connection_row.get("id") or "") != connection_id.strip()
            or str(connection_row.get("connector_key") or "") != "facebook"
        ):
            raise TenantFacebookHealthContextError("integration_connection_resource_binding_mismatch")

        raw_capabilities = connection_row.get("granted_capabilities") or []
        if not isinstance(raw_capabilities, (list, tuple)) or any(
            not isinstance(item, str) for item in raw_capabilities
        ):
            raise TenantFacebookHealthContextError("integration_connection_capabilities_invalid")
        try:
            connection_state = ConnectionState(str(connection_row.get("state") or ""))
            autonomy_mode = AutonomyMode(str(connection_row.get("autonomy_mode") or ""))
            connection = IntegrationConnection(
                workspace_id=workspace_id,
                connector_key="facebook",
                state=connection_state,
                granted_capabilities=tuple(raw_capabilities),
                autonomy_mode=autonomy_mode,
                connection_id=connection_id.strip(),
                brand_id=binding.brand_id,
                social_account_id=binding.account_id,
            )
        except Exception as exc:
            raise TenantFacebookHealthContextError("integration_connection_contract_invalid") from exc
        if connection.state not in self._ALLOWED_CONNECTION_STATES:
            raise TenantFacebookHealthContextError("integration_connection_not_observable")
        if not connection.has_capability("lookup"):
            raise TenantFacebookHealthContextError("facebook_lookup_capability_missing")

        registry_row = _one_row(
            self._client,
            "connector_certification_registry",
            workspace_id=workspace_id,
            filters={
                "connection_id": connection.connection_id,
                "connector_key": "facebook",
            },
        )
        registry_brand_id = str(registry_row.get("brand_id") or "")
        registry_account_id = str(registry_row.get("social_account_id") or "")
        if (
            registry_brand_id != binding.brand_id
            or registry_account_id != binding.account_id
            or str(registry_row.get("connector_version") or "") == ""
        ):
            raise TenantFacebookHealthContextError("certification_registry_resource_binding_mismatch")

        try:
            registry_state = RegistryState(str(registry_row.get("registry_state") or ""))
            certification_status = CertificationStatus(
                str(registry_row.get("certification_status") or "")
            )
            certified_at = _parse_datetime(registry_row.get("certified_at"), "certified_at")
            expires_at = _parse_datetime(
                registry_row.get("expires_at"), "expires_at", optional=True
            )
            updated_at = _parse_datetime(
                registry_row.get("updated_at"), "updated_at", optional=True
            ) or now
            fingerprint = str(registry_row.get("certification_fingerprint") or "")
            if len(fingerprint) != 64 or any(
                char not in "0123456789abcdef" for char in fingerprint
            ):
                raise TenantFacebookHealthContextError("certification_fingerprint_invalid")
            evidence = CertificationEvidence(
                certification_version=str(registry_row.get("certification_version") or ""),
                connector_key="facebook",
                connector_version=str(registry_row["connector_version"]),
                fingerprint=fingerprint,
                status=certification_status,
                live_provider_verified=registry_row.get("live_provider_verified") is True,
                certified_at=certified_at,
                expires_at=expires_at,
                evidence_ref=str(registry_row.get("evidence_ref") or ""),
            )
            registry = ConnectorRegistryRecord(
                workspace_id=workspace_id,
                connection_id=connection.connection_id,
                connector_key="facebook",
                connector_version=evidence.connector_version,
                state=registry_state,
                evidence=evidence,
                generation=int(registry_row.get("generation") or 1),
                reason=str(registry_row.get("reason") or ""),
                updated_at=updated_at,
                brand_id=binding.brand_id,
                social_account_id=binding.account_id,
            )
        except TenantFacebookHealthContextError:
            raise
        except Exception as exc:
            raise TenantFacebookHealthContextError("certification_registry_contract_invalid") from exc

        if registry.state not in self._ALLOWED_REGISTRY_STATES:
            raise TenantFacebookHealthContextError("certification_registry_not_observable")
        if evidence.status != CertificationStatus.PASS or not evidence.live_provider_verified:
            raise TenantFacebookHealthContextError("live_facebook_certification_required")
        if evidence.expires_at is not None and now >= evidence.expires_at:
            raise TenantFacebookHealthContextError("facebook_certification_expired")

        contract = ConnectorContract(
            connector_key="facebook",
            provider="facebook",
            version=registry.connector_version,
            capabilities=("lookup",),
            inbound_events=(),
            outbound_actions=(),
            authentication_method="oauth2",
            risk_by_action={},
            tenant_scoped=True,
            certified=True,
        )
        return TenantFacebookHealthContext(
            workspace_id=workspace_id,
            binding=binding,
            connection=connection,
            contract=contract,
            registry=registry,
        )


__all__ = [
    "TenantFacebookHealthContextError",
    "TenantFacebookHealthContext",
    "TenantFacebookHealthContextResolver",
]
