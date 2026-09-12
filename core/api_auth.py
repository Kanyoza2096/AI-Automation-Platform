"""
DEPRECATED ADAPTER — Phase A elimination.

Canonical authentication: core.species_auth.SpeciesAuth
Canonical tokens:       core.api_tokens.APITokenManager

This module remains only so older imports of `api_auth` keep working.
Do not add new validation or persistence logic here.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import HTTPException, Request

logger = logging.getLogger("kanyoza.core.api_auth")


class APIAuth:
    """Thin adapter over SpeciesAuth + APITokenManager."""

    def __init__(self, supabase_client=None) -> None:
        self.supabase = supabase_client

    def set_supabase_client(self, client) -> None:
        self.supabase = client

    def validate_key(self, api_key: str) -> Dict[str, Any]:
        """Delegate to SpeciesAuth. Returns legacy dict shape for old callers."""
        from core.species_auth import species_auth

        identity = species_auth.validate(bearer_token=api_key or "")
        if not identity.valid:
            return {"valid": False, "error": identity.error or "Unauthorized"}
        return {
            "valid": True,
            "workspace_id": identity.workspace_id,
            "permissions": list(identity.permissions or ["read"]),
            "name": identity.name or identity.method,
            "method": identity.method,
            "is_species": identity.is_species,
        }

    async def require_auth(self, request: Request) -> Dict[str, Any]:
        from core.species_auth import species_auth, extract_token_from_request
        from core.workspace_context import set_workspace_context

        bearer, api_key = extract_token_from_request(request)
        identity = species_auth.validate(
            bearer_token=bearer or api_key,
            api_key_header=api_key,
            supabase_client=self.supabase,
        )
        if not identity.valid:
            raise HTTPException(status_code=401, detail=identity.error or "Unauthorized")
        if identity.workspace_id:
            set_workspace_context(workspace_id=identity.workspace_id)
        return {
            "valid": True,
            "workspace_id": identity.workspace_id,
            "permissions": list(identity.permissions or []),
            "method": identity.method,
            "is_species": identity.is_species,
        }

    def require_permission(self, permission: str):
        async def checker(request: Request):
            result = await self.require_auth(request)
            if result.get("is_species"):
                return result
            if permission not in result.get("permissions", []) and "admin" not in result.get("permissions", []) and "owner" not in result.get("permissions", []):
                raise HTTPException(status_code=403, detail=f"Missing permission: {permission}")
            return result
        return checker

    def generate_key(self, tenant_id: str, name: str = "Default", permissions: list = None) -> str:
        """Create via APITokenManager only — never api_keys table."""
        from core.api_tokens import api_token_manager

        result = api_token_manager.create_token(
            workspace_id=tenant_id,
            name=name,
            permissions=permissions or ["read"],
        )
        return result["token"]


api_auth = APIAuth()
