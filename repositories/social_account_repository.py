import logging
from typing import List, Optional
from cryptography.fernet import Fernet

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
        """Return a new entity with encrypted credentials for storage."""
        dump = entity.model_dump()
        dump['access_token'] = self._encrypt(dump['access_token'])
        dump['refresh_token'] = self._encrypt(dump['refresh_token'])
        dump['app_secret'] = self._encrypt(dump['app_secret'])
        return SocialAccount(**dump)

    def _decrypt_entity(self, data: dict) -> SocialAccount:
        """Decrypt credentials when loading from storage."""
        data['access_token'] = self._decrypt(data.get('access_token', ''))
        data['refresh_token'] = self._decrypt(data.get('refresh_token', ''))
        data['app_secret'] = self._decrypt(data.get('app_secret', ''))
        return SocialAccount(**data)

    def find_by_id(self, record_id: str) -> Optional[SocialAccount]:
        if not self.supabase:
            return self._in_memory.get(record_id)
        
        def _fetch():
            res = self.supabase.table('social_accounts').select('*').eq('id', record_id).execute()
            if res.data:
                return self._decrypt_entity(res.data[0])
            return None
            
        return self._safe_call(_fetch)

    def save(self, entity: SocialAccount) -> SocialAccount:
        if not self.supabase:
            self._in_memory[entity.id] = entity
            return entity
            
        def _upsert():
            encrypted_entity = self._encrypt_entity(entity)
            data = encrypted_entity.model_dump(mode='json')
            res = self.supabase.table('social_accounts').upsert(data).execute()
            if res.data:
                return self._decrypt_entity(res.data[0])
            return entity
            
        return self._safe_call(_upsert, default=entity)

    def delete(self, record_id: str) -> bool:
        if not self.supabase:
            if record_id in self._in_memory:
                del self._in_memory[record_id]
                return True
            return False
            
        def _delete():
            self.supabase.table('social_accounts').delete().eq('id', record_id).execute()
            return True
            
        return self._safe_call(_delete, default=False)

    def find_by_workspace(self, workspace_id: str) -> List[SocialAccount]:
        if not self.supabase:
            return [a for a in self._in_memory.values() if a.workspace_id == workspace_id]
            
        def _fetch():
            res = self.supabase.table('social_accounts').select('*').eq('workspace_id', workspace_id).execute()
            return [self._decrypt_entity(row) for row in res.data]
            
        return self._safe_call(_fetch, default=[])

    def find_by_brand(self, brand_id: str) -> List[SocialAccount]:
        """Find all social accounts linked to a specific brand."""
        if not self.supabase:
            return [a for a in self._in_memory.values() if a.brand_id == brand_id]
            
        def _fetch():
            res = self.supabase.table('social_accounts').select('*').eq('brand_id', brand_id).execute()
            return [self._decrypt_entity(row) for row in res.data]
            
        return self._safe_call(_fetch, default=[])

    def find_by_brand_and_platform(self, brand_id: str, platform: str) -> Optional[SocialAccount]:
        """Find a social account for a specific brand and platform."""
        if not self.supabase:
            for a in self._in_memory.values():
                if a.brand_id == brand_id and a.platform == platform:
                    return a
            return None
            
        def _fetch():
            res = self.supabase.table('social_accounts') \
                .select('*') \
                .eq('brand_id', brand_id) \
                .eq('platform', platform) \
                .limit(1) \
                .execute()
            if res.data:
                return self._decrypt_entity(res.data[0])
            return None
            
        return self._safe_call(_fetch)

    def find_by_platform(self, platform: str) -> List[SocialAccount]:
        """Find all social accounts for a specific platform."""
        if not self.supabase:
            return [a for a in self._in_memory.values() if a.platform == platform]
            
        def _fetch():
            res = self.supabase.table('social_accounts').select('*').eq('platform', platform).execute()
            return [self._decrypt_entity(row) for row in res.data]
            
        return self._safe_call(_fetch, default=[])

    def find_enabled(self) -> List[SocialAccount]:
        """Find all enabled social accounts."""
        if not self.supabase:
            return [a for a in self._in_memory.values() if a.enabled]
            
        def _fetch():
            res = self.supabase.table('social_accounts').select('*').eq('enabled', True).execute()
            return [self._decrypt_entity(row) for row in res.data]
            
        return self._safe_call(_fetch, default=[])
