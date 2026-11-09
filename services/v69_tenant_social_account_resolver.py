"""Strict tenant-to-brand-to-social-account resolution for connector runtimes.

This resolver requires an explicitly injected server-side Supabase client.
It never uses the repositories' in-memory fallback when a database query
fails, and it returns credentials only inside a repr-redacted binding object.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from models.brand import Brand
from models.social_account import SocialAccount
from repositories.social_account_repository import SocialAccountRepository


class TenantAccountResolutionError(RuntimeError):
    """A tenant-owned brand/account could not be resolved safely."""


@dataclass(frozen=True)
class TenantSocialAccountBinding:
    workspace_id: str
    brand_id: str
    account_id: str
    platform: str
    platform_account_id: str
    brand: Brand = field(repr=False, compare=False)
    account: SocialAccount = field(repr=False, compare=False)

    def public_identity(self) -> dict[str, str]:
        """Return the non-secret identity needed for logs and audit metadata."""
        return {
            "workspace_id": self.workspace_id,
            "brand_id": self.brand_id,
            "brand_name": self.brand.name,
            "account_id": self.account_id,
            "platform": self.platform,
            "platform_account_id": self.platform_account_id,
        }


class TenantSocialAccountResolver:
    """Resolve exactly one social account inside an explicitly supplied workspace."""

    def __init__(self, supabase_client: Any) -> None:
        if supabase_client is None:
            raise TenantAccountResolutionError("explicit_supabase_client_required")
        self._client = supabase_client
        self._account_hydrator = SocialAccountRepository(supabase_client=supabase_client)

    def resolve(
        self,
        *,
        workspace_id: str,
        brand_id: str,
        platform: str,
    ) -> TenantSocialAccountBinding:
        workspace_id = str(workspace_id or "").strip()
        brand_id = str(brand_id or "").strip()
        platform = str(platform or "").strip().lower()
        if not workspace_id or not brand_id or not platform:
            raise TenantAccountResolutionError("workspace_brand_platform_required")

        try:
            brand_response = (
                self._client.table("brands")
                .select("*")
                .eq("id", brand_id)
                .eq("workspace_id", workspace_id)
                .limit(2)
                .execute()
            )
        except Exception as exc:
            raise TenantAccountResolutionError("brand_lookup_failed") from exc

        brands = brand_response.data or []
        if not brands:
            raise TenantAccountResolutionError("brand_not_found_in_workspace")
        if len(brands) != 1:
            raise TenantAccountResolutionError("ambiguous_brand_identity")
        try:
            brand = Brand(**brands[0])
        except Exception as exc:
            raise TenantAccountResolutionError("brand_record_invalid") from exc
        if brand.id != brand_id or brand.workspace_id != workspace_id:
            raise TenantAccountResolutionError("brand_workspace_mismatch")

        try:
            account_response = (
                self._client.table("social_accounts")
                .select("*")
                .eq("workspace_id", workspace_id)
                .eq("brand_id", brand_id)
                .eq("platform", platform)
                .limit(2)
                .execute()
            )
        except Exception as exc:
            raise TenantAccountResolutionError("social_account_lookup_failed") from exc

        accounts = account_response.data or []
        if not accounts:
            raise TenantAccountResolutionError("social_account_not_found")
        if len(accounts) != 1:
            raise TenantAccountResolutionError("ambiguous_social_account")

        try:
            account = self._account_hydrator.hydrate_row(accounts[0])
        except Exception as exc:
            raise TenantAccountResolutionError("social_account_credentials_unavailable") from exc

        account_platform = (
            account.platform.value
            if hasattr(account.platform, "value")
            else str(account.platform)
        )
        if (
            account.workspace_id != workspace_id
            or account.brand_id != brand_id
            or account_platform.lower() != platform
        ):
            raise TenantAccountResolutionError("social_account_tenant_binding_mismatch")

        return TenantSocialAccountBinding(
            workspace_id=workspace_id,
            brand_id=brand_id,
            account_id=account.id,
            platform=platform,
            platform_account_id=account.platform_account_id,
            brand=brand,
            account=account,
        )


__all__ = [
    "TenantAccountResolutionError",
    "TenantSocialAccountBinding",
    "TenantSocialAccountResolver",
]
