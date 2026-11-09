"""Tenant-safe composition of a resolved Facebook Page account and runtime gateway.

This factory performs no provider request. It only assembles an account-bound
FacebookConnector behind the governed runtime gateway after checking that the
resolved brand/account binding matches the workspace-owned connection registry.
"""
from __future__ import annotations

from typing import Any

from models.social_account import Platform
from services.connectors.facebook_connector import FacebookConnector
from services.connectors.governed_connector import GovernedConnector
from services.v69_connector_runtime_gateway import RuntimeConnectorGateway
from services.v69_tenant_social_account_resolver import TenantSocialAccountBinding


class TenantFacebookCompositionError(ValueError):
    """A Facebook connector cannot be safely composed for this tenant."""


class TenantFacebookConnectorFactory:
    def __init__(self, *, facebook_service: Any, ledger: Any) -> None:
        if facebook_service is None:
            raise TenantFacebookCompositionError("facebook_service_required")
        if ledger is None:
            raise TenantFacebookCompositionError("execution_ledger_required")
        self._facebook_service = facebook_service
        self._ledger = ledger

    def build_gateway(
        self,
        *,
        binding: TenantSocialAccountBinding,
        connection: Any,
        contract: Any,
        registry: Any,
    ) -> RuntimeConnectorGateway:
        if not isinstance(binding, TenantSocialAccountBinding):
            raise TenantFacebookCompositionError("verified_tenant_account_binding_required")

        account = binding.account
        brand = binding.brand
        account_platform = (
            account.platform.value
            if hasattr(account.platform, "value")
            else str(account.platform)
        )
        if (
            binding.platform != Platform.FACEBOOK.value
            or account_platform.lower() != Platform.FACEBOOK.value
        ):
            raise TenantFacebookCompositionError("facebook_account_required")
        if (
            brand.id != binding.brand_id
            or brand.workspace_id != binding.workspace_id
            or account.id != binding.account_id
            or account.workspace_id != binding.workspace_id
            or account.brand_id != binding.brand_id
            or account.platform_account_id != binding.platform_account_id
        ):
            raise TenantFacebookCompositionError("tenant_account_binding_mismatch")
        if (
            connection.workspace_id != binding.workspace_id
            or registry.workspace_id != binding.workspace_id
            or connection.connection_id != registry.connection_id
            or connection.connector_key != Platform.FACEBOOK.value
            or registry.connector_key != Platform.FACEBOOK.value
            or contract.connector_key != Platform.FACEBOOK.value
        ):
            raise TenantFacebookCompositionError("tenant_connection_registry_mismatch")

        # Supplying the resolved SocialAccount is mandatory. FacebookConnector
        # will use this Page ID/token pair and must not fall back to process
        # settings for health or validation.
        connector = FacebookConnector(
            self._facebook_service,
            social_account=account,
        )
        governed = GovernedConnector(
            connector,
            workspace_id=binding.workspace_id,
            ledger=self._ledger,
        )
        return RuntimeConnectorGateway(
            workspace_id=binding.workspace_id,
            connection=connection,
            contract=contract,
            registry=registry,
            governed=governed,
        )


__all__ = ["TenantFacebookCompositionError", "TenantFacebookConnectorFactory"]
