import logging
from typing import List, Optional

from repositories.base import Repository
from models.social_account import SocialAccount
from config.settings import get_settings

logger = logging.getLogger("kanyoza.repositories.social_account")

class SocialAccountRepository(Repository[SocialAccount]):
    """Supabase + In-Memory fallback repository for Social Accounts.
    Encrypts access_token and app_secret at rest using Fernet."""

    def __init__(self, supabase_client=None):
        self.supabase = supabase_client
        self._in_memory = {}

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
        dump['access_token'] = self._encrypt(dump['access_token'])
        dump['refresh_token'] = self._encrypt(dump['refresh_token'])
        dump['app_secret'] = self._encrypt(dump['app_secret'])
        return SocialAccount(**dump)

    def _decrypt_entity(self, data: dict) -> SocialAccount:
        data['access_token'] = self._decrypt(data.get('access_token', ''))
        data['refresh_token'] = self._decrypt(data.get('refresh_token', ''))
        data['app_secret'] = self._decrypt(data.get('app_secret', ''))
        return SocialAccount(**data)

    def find_by_id(self, record_id: str) -> Optional[SocialAccount]:
        if not self.supabase:
            return self._in_memory.get(record_id)
        try:
            res = self.supabase.table('social_accounts').select('*').eq('id', record_id).execute()
            if res.data:
                return self._decrypt_entity(res.data[0])
            return None
        except Exception as e:
            logger.error("Failed to find social account by id %s: %s", record_id, e)
            return self._in_memory.get(record_id)

    def save(self, entity: SocialAccount) -> SocialAccount:
        if not self.supabase:
            self._in_memory[entity.id] = entity
            return entity
        try:
            encrypted_entity = self._encrypt_entity(entity)
            data = encrypted_entity.model_dump(mode='json')
            res = self.supabase.table('social_accounts').upsert(data).execute()
            if res.data:
                return self._decrypt_entity(res.data[0])
            return entity
        except Exception as e:
            logger.error("Failed to save social account %s: %s", entity.id, e)
            self._in_memory[entity.id] = entity
            return entity

    def delete(self, record_id: str) -> bool:
        if not self.supabase:
            if record_id in self._in_memory:
                del self._in_memory[record_id]
                return True
            return False
        try:
            self.supabase.table('social_accounts').delete().eq('id', record_id).execute()
            return True
        except Exception as e:
            logger.error("Failed to delete social account %s: %s", record_id, e)
            if record_id in self._in_memory:
                del self._in_memory[record_id]
                return True
            return False

    def find_by_workspace(self, workspace_id: str) -> List[SocialAccount]:
        if not self.supabase:
            return [a for a in self._in_memory.values() if a.workspace_id == workspace_id]
        try:
            res = self.supabase.table('social_accounts').select('*').eq('workspace_id', workspace_id).execute()
            return [self._decrypt_entity(row) for row in (res.data or [])]
        except Exception as e:
            logger.error("Failed to find social accounts by workspace %s: %s", workspace_id, e)
            return [a for a in self._in_memory.values() if a.workspace_id == workspace_id]

    def find_by_brand(self, brand_id: str) -> List[SocialAccount]:
        if not self.supabase:
            return [a for a in self._in_memory.values() if a.brand_id == brand_id]
        try:
            res = self.supabase.table('social_accounts').select('*').eq('brand_id', brand_id).execute()
            return [self._decrypt_entity(row) for row in (res.data or [])]
        except Exception as e:
            logger.error("Failed to find social accounts by brand %s: %s", brand_id, e)
            return [a for a in self._in_memory.values() if a.brand_id == brand_id]

    def find_by_brand_and_platform(self, brand_id: str, platform: str) -> Optional[SocialAccount]:
        if not self.supabase:
            for a in self._in_memory.values():
                if a.brand_id == brand_id and a.platform == platform:
                    return a
            return None
        try:
            res = self.supabase.table('social_accounts') \
                .select('*') \
                .eq('brand_id', brand_id) \
                .eq('platform', platform) \
                .limit(1) \
                .execute()
            if res.data:
                return self._decrypt_entity(res.data[0])
            return None
        except Exception as e:
            logger.error("Failed to find social account by brand %s and platform %s: %s", brand_id, platform, e)
            for a in self._in_memory.values():
                if a.brand_id == brand_id and a.platform == platform:
                    return a
            return None

    def find_by_platform(self, platform: str) -> List[SocialAccount]:
        if not self.supabase:
            return [a for a in self._in_memory.values() if a.platform == platform]
        try:
            res = self.supabase.table('social_accounts').select('*').eq('platform', platform).execute()
            return [self._decrypt_entity(row) for row in (res.data or [])]
        except Exception as e:
            logger.error("Failed to find social accounts by platform %s: %s", platform, e)
            return [a for a in self._in_memory.values() if a.platform == platform]

    def update_health_status(self, record_id: str, status: str) -> None:
    """Update health status without loading the full entity (avoids frozen instance error)."""
    from datetime import datetime, timezone
    if not self.supabase:
        if record_id in self._in_memory:
            # In-memory dicts aren't frozen, so direct assignment works
            self._in_memory[record_id].health_status = status
        return
    try:
        self.supabase.table('social_accounts').update({
            'health_status': status,
            'last_health_check': datetime.now(timezone.utc).isoformat()
        }).eq('id', record_id).execute()
        logger.info("Health status updated for %s: %s", record_id, status)
    except Exception as e:
        logger.error("Failed to update health status for %s: %s", record_id, e)

    def find_enabled(self) -> List[SocialAccount]:
        if not self.supabase:
            return [a for a in self._in_memory.values() if a.enabled]
        try:
            res = self.supabase.table('social_accounts').select('*').eq('enabled', True).execute()
            return [self._decrypt_entity(row) for row in (res.data or [])]
        except Exception as e:
            logger.error("Failed to find enabled social accounts: %s", e)
            return [a for a in self._in_memory.values() if a.enabled]
