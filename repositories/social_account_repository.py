"""
Social Account Repository — Architecture §33-34.

Provides data access for social account records.
Uses Supabase service-role client for backend credential operations.
In-memory fallback is ONLY for development/testing — never in production.

Architecture §9 — Remove Dangerous Silent In-Memory Fallback:
  - NOT_FOUND is distinct from DATABASE_UNAVAILABLE
  - Production mode never silently degrades to volatile memory
  - In-memory storage is for tests/local dev only

Architecture §31 — Tenant Isolation:
  Every query scoped by brand_id + platform.
  Never: accounts[0]
  Always: find_by_brand_and_platform(brand_id, "facebook")
"""

import logging
import os

from typing import List, Optional

from repositories.base import Repository
from models.social_account import SocialAccount
from core.errors import (
    DatabaseUnavailableError,
    DatabaseQueryError,
    DatabaseAuthError,
    NoSocialAccountError,
    ErrorCode,
)

logger = logging.getLogger("kanyoza.repositories.social_account")


class SocialAccountRepository(Repository[SocialAccount]):
    """Supabase-backed repository for Social Accounts.
    
    Uses the best available Supabase client — prefers service_role for
    backend credential operations, falls back to anon client.
    
    In-memory fallback ONLY activates when KANYOZA_ENV=development
    or when explicitly enabled for testing.
    """

    def __init__(self, supabase_client=None):
        self.supabase = supabase_client
        self._in_memory: dict = {}
        from config.settings import settings
        self._production = settings.is_production

    # ═══════════════════════════════════════════════════════════════════
    # Client Resolution
    # ═══════════════════════════════════════════════════════════════════

    def _get_client(self):
        """Get the best available Supabase client.
        
        Prefers service_role for backend credential operations.
        Returns None only if no client is configured.
        """
        if self.supabase:
            return self.supabase
        try:
            from services.supabase import supabase_service
            if supabase_service and getattr(supabase_service, 'service_client', None):
                return supabase_service.service_client
            if supabase_service and supabase_service.client:
                return supabase_service.client
        except Exception:
            pass
        return None

    def _require_client(self):
        """Get a Supabase client or raise a typed error.
        
        Architecture §9: Never silently fall back to in-memory in production.
        """
        client = self._get_client()
        if client is None:
            is_prod = self._production or (os.getenv("KANYOZA_ENV") or "").lower() == "production"
            if is_prod:
                raise DatabaseUnavailableError(
                    message="Supabase client not configured — cannot perform database operation in production",
                    context={"operation": "social_account_repository"},
                )
            logger.warning("Supabase client not available — using in-memory fallback (non-production)")
        return client

    # ═══════════════════════════════════════════════════════════════════
    # Encryption helpers
    # ═══════════════════════════════════════════════════════════════════

    def _encrypt(self, value: str) -> str:
        if not value:
            return ""
        from core.credential_vault import credential_vault
        return credential_vault.encrypt(value)

    def _decrypt(self, value: str) -> str:
        if not value:
            return ""
        from core.credential_vault import credential_vault
        return credential_vault.decrypt(value)

    def _encrypt_entity(self, entity: SocialAccount) -> SocialAccount:
        dump = entity.model_dump()
        dump['access_token'] = self._encrypt(dump.get('access_token', ''))
        dump['refresh_token'] = self._encrypt(dump.get('refresh_token', ''))
        dump['app_secret'] = self._encrypt(dump.get('app_secret', ''))
        return SocialAccount(**dump)

    def _decrypt_entity(self, data: dict) -> SocialAccount:
        data = dict(data)
        data['access_token'] = self._decrypt(data.get('access_token', ''))
        data['refresh_token'] = self._decrypt(data.get('refresh_token', ''))
        data['app_secret'] = self._decrypt(data.get('app_secret', ''))
        return SocialAccount(**data)

    # ═══════════════════════════════════════════════════════════════════
    # CRUD Operations
    # ═══════════════════════════════════════════════════════════════════

    def find_by_id(self, record_id: str) -> Optional[SocialAccount]:
        """Find a social account by ID.
        
        Returns None if not found.
        Raises DatabaseUnavailableError if database is unreachable in production.
        """
        client = self._require_client()
        if not client:
            return self._in_memory.get(record_id)
        try:
            res = client.table('social_accounts').select('*').eq('id', record_id).execute()
            if res.data:
                return self._decrypt_entity(res.data[0])
            return None
        except Exception as e:
            logger.error("Failed to find social account by id %s: %s", record_id, e)
            if self._production:
                raise DatabaseQueryError(
                    message=f"Failed to query social account by id: {record_id}",
                    cause=e,
                    context={"record_id": record_id},
                )
            return self._in_memory.get(record_id)

    def save(self, entity: SocialAccount) -> SocialAccount:
        """Create or update a social account."""
        client = self._require_client()
        if not client:
            self._in_memory[entity.id] = entity
            return entity
        try:
            encrypted_entity = self._encrypt_entity(entity)
            data = encrypted_entity.model_dump(mode='json')
            res = client.table('social_accounts').upsert(data).execute()
            if res.data:
                return self._decrypt_entity(res.data[0])
            return entity
        except Exception as e:
            logger.error("Failed to save social account %s: %s", entity.id, e)
            if self._production:
                raise DatabaseQueryError(
                    message=f"Failed to save social account: {entity.id}",
                    cause=e,
                    context={"entity_id": entity.id},
                )
            self._in_memory[entity.id] = entity
            return entity

    def delete(self, record_id: str) -> bool:
        """Delete a social account by ID."""
        client = self._require_client()
        if not client:
            if record_id in self._in_memory:
                del self._in_memory[record_id]
                return True
            return False
        try:
            client.table('social_accounts').delete().eq('id', record_id).execute()
            return True
        except Exception as e:
            logger.error("Failed to delete social account %s: %s", record_id, e)
            if self._production:
                raise DatabaseQueryError(
                    message=f"Failed to delete social account: {record_id}",
                    cause=e,
                    context={"record_id": record_id},
                )
            if record_id in self._in_memory:
                del self._in_memory[record_id]
                return True
            return False

    # ═══════════════════════════════════════════════════════════════════
    # Query Methods
    # ═══════════════════════════════════════════════════════════════════

    def find_by_workspace(self, workspace_id: str) -> List[SocialAccount]:
        """Find all social accounts for a workspace."""
        client = self._require_client()
        if not client:
            return [a for a in self._in_memory.values() if a.workspace_id == workspace_id]
        try:
            res = client.table('social_accounts').select('*').eq('workspace_id', workspace_id).execute()
            return [self._decrypt_entity(row) for row in (res.data or [])]
        except Exception as e:
            logger.error("Failed to find social accounts by workspace %s: %s", workspace_id, e)
            if self._production:
                raise DatabaseQueryError(
                    message=f"Failed to query social accounts by workspace: {workspace_id}",
                    cause=e,
                    context={"workspace_id": workspace_id},
                )
            return [a for a in self._in_memory.values() if a.workspace_id == workspace_id]

    def find_by_brand(self, brand_id: str) -> List[SocialAccount]:
        """Find all social accounts for a brand."""
        client = self._require_client()
        if not client:
            return [a for a in self._in_memory.values() if a.brand_id == brand_id]
        try:
            res = client.table('social_accounts').select('*').eq('brand_id', brand_id).execute()
            return [self._decrypt_entity(row) for row in (res.data or [])]
        except Exception as e:
            logger.error("Failed to find social accounts by brand %s: %s", brand_id, e)
            if self._production:
                raise DatabaseQueryError(
                    message=f"Failed to query social accounts by brand: {brand_id}",
                    cause=e,
                    context={"brand_id": brand_id},
                )
            return [a for a in self._in_memory.values() if a.brand_id == brand_id]

    def find_by_brand_and_platform(self, brand_id: str, platform: str) -> Optional[SocialAccount]:
        """Find a social account by brand and platform.
        
        This is the CRITICAL method for multi-tenant publishing.
        Architecture §11 — Multi-Tenant Account Resolution:
          brand_id + platform → deterministic account selection.
          Never: accounts[0].
        
        Returns None if no account exists.
        Raises DatabaseUnavailableError if database is unreachable in production.
        """
        client = self._require_client()
        if not client:
            for a in self._in_memory.values():
                if a.brand_id == brand_id and a.platform == platform:
                    return a
            return None
        try:
            res = client.table('social_accounts') \
                .select('*') \
                .eq('brand_id', brand_id) \
                .eq('platform', platform) \
                .limit(1) \
                .execute()
            if res.data:
                return self._decrypt_entity(res.data[0])
            return None
        except Exception as e:
            logger.error(
                "Failed to find social account by brand %s and platform %s: %s",
                brand_id, platform, e,
            )
            if self._production:
                raise DatabaseQueryError(
                    message=f"Failed to query social account: brand={brand_id}, platform={platform}",
                    cause=e,
                    context={"brand_id": brand_id, "platform": platform},
                )
            for a in self._in_memory.values():
                if a.brand_id == brand_id and a.platform == platform:
                    return a
            return None

    def find_by_platform(self, platform: str) -> List[SocialAccount]:
        """Find all social accounts for a given platform."""
        client = self._require_client()
        if not client:
            return [a for a in self._in_memory.values() if a.platform == platform]
        try:
            res = client.table('social_accounts').select('*').eq('platform', platform).execute()
            return [self._decrypt_entity(row) for row in (res.data or [])]
        except Exception as e:
            logger.error("Failed to find social accounts by platform %s: %s", platform, e)
            if self._production:
                raise DatabaseQueryError(
                    message=f"Failed to query social accounts by platform: {platform}",
                    cause=e,
                    context={"platform": platform},
                )
            return [a for a in self._in_memory.values() if a.platform == platform]

    def update_health_status(self, record_id: str, status: str) -> None:
        """Update the health status of a social account."""
        from datetime import datetime, timezone
        client = self._require_client()
        if not client:
            if record_id in self._in_memory:
                self._in_memory[record_id].health_status = status
            return
        try:
            client.table('social_accounts').update({
                'health_status': status,
                'last_health_check': datetime.now(timezone.utc).isoformat()
            }).eq('id', record_id).execute()
            logger.info("Health status updated for %s: %s", record_id, status)
        except Exception as e:
            logger.error("Failed to update health status for %s: %s", record_id, e)
            if self._production:
                raise DatabaseQueryError(
                    message=f"Failed to update health status: {record_id}",
                    cause=e,
                    context={"record_id": record_id, "status": status},
                )

    def find_enabled(self) -> List[SocialAccount]:
        """Find all enabled social accounts."""
        client = self._require_client()
        if not client:
            return [a for a in self._in_memory.values() if a.enabled]
        try:
            res = client.table('social_accounts').select('*').eq('enabled', True).execute()
            return [self._decrypt_entity(row) for row in (res.data or [])]
        except Exception as e:
            logger.error("Failed to find enabled social accounts: %s", e)
            if self._production:
                raise DatabaseQueryError(
                    message="Failed to query enabled social accounts",
                    cause=e,
                )
            return [a for a in self._in_memory.values() if a.enabled]
