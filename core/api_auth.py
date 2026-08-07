"""
API Authentication v1.0 (FastAPI)
- API key validation
- Rate limit per key
- Permission checking
"""

import hashlib
import logging
from datetime import datetime, timezone

from fastapi import Request, HTTPException, Depends

import hmac

logger = logging.getLogger("kanyoza.core.api_auth")


class APIAuth:
    """API key authentication manager."""

    def __init__(self, supabase_client=None):
        self.supabase = supabase_client

    def validate_key(self, api_key: str) -> dict:
        """Validate an API key or token, and return workspace / permissions info."""
        from config.settings import settings
        
        # 1. Master API token check (constant time)
        if settings.master_api_token and api_key and hmac.compare_digest(api_key, settings.master_api_token):
            return {
                'valid': True,
                'workspace_id': settings.default_workspace_id or 'default',
                'permissions': ['read', 'write', 'admin', 'owner'],
                'name': 'Master Token',
            }

        # 2. Workspace-scoped token check via APITokenManager
        try:
            from core.api_tokens import api_token_manager
            record = api_token_manager.validate_token(api_key)
            if record:
                return {
                    'valid': True,
                    'workspace_id': record['workspace_id'],
                    'permissions': record.get('permissions', ['read']),
                    'name': record.get('name', 'Scoped Token'),
                }
        except Exception as e:
            logger.error("Workspace API token validation error: %s", e)

        # 3. Fallback to legacy api_keys table for backwards compatibility
        if not self.supabase:
            return {'valid': False, 'error': 'Auth service unavailable'}

        key_hash = hashlib.sha256(api_key.encode()).hexdigest()

        try:
            result = (self.supabase.table('api_keys')
                .select('*, tenants!inner(*)')
                .eq('key_hash', key_hash)
                .maybe_single()
                .execute())

            if not result.data:
                return {'valid': False, 'error': 'Invalid API key'}

            key_data = result.data

            if key_data.get('expires_at') and datetime.fromisoformat(key_data['expires_at']) < datetime.now(timezone.utc):
                return {'valid': False, 'error': 'API key expired'}

            self.supabase.table('api_keys').update({
                'last_used_at': datetime.now(timezone.utc).isoformat()
            }).eq('id', key_data['id']).execute()

            return {
                'valid': True,
                'workspace_id': key_data.get('tenant_id', 'default'),
                'permissions': key_data.get('permissions', ['read']),
                'tenant': key_data.get('tenants', {}),
            }

        except Exception as e:
            logger.error("Legacy API key validation error: %s", e)
            return {'valid': False, 'error': 'Validation failed'}

    async def __call__(self, request: Request):
        """FastAPI dependency — validates the API key from request headers."""
        api_key = request.headers.get('X-API-Key', '')
        if not api_key and request.headers.get('Authorization', '').startswith('Bearer '):
            api_key = request.headers.get('Authorization', '')[7:]

        if not api_key:
            raise HTTPException(status_code=401, detail='API key required')

        validation = self.validate_key(api_key)
        if not validation['valid']:
            raise HTTPException(status_code=401, detail=validation.get('error', 'Unauthorized'))

        # Set workspace context
        from core.workspace_context import set_workspace_context
        set_workspace_context(workspace_id=validation.get('workspace_id'))

        return validation

    def require_permission(self, permission: str):
        """FastAPI dependency — checks for specific permission."""
        async def checker(request: Request):
            # Re-validate to get permissions
            api_key = request.headers.get('X-API-Key', '')
            if not api_key and request.headers.get('Authorization', '').startswith('Bearer '):
                api_key = request.headers.get('Authorization', '')[7:]
            
            if not api_key:
                raise HTTPException(status_code=401, detail='Authentication required')
            
            validation = self.validate_key(api_key)
            if not validation['valid']:
                raise HTTPException(status_code=401, detail=validation.get('error', 'Unauthorized'))
            
            if permission not in validation.get('permissions', []):
                raise HTTPException(status_code=403, detail=f'Missing permission: {permission}')
            
            return validation
        return checker

    def generate_key(self, tenant_id: str, name: str = 'Default', permissions: list = None) -> str:
        """Generate a new API key for a tenant."""
        import secrets
        api_key = f"kny_{secrets.token_hex(24)}"
        key_hash = hashlib.sha256(api_key.encode()).hexdigest()

        if self.supabase:
            try:
                self.supabase.table('api_keys').insert({
                    'id': f"apikey-{secrets.token_hex(4)}",
                    'tenant_id': tenant_id,
                    'key_hash': key_hash,
                    'name': name,
                    'permissions': permissions or ['read'],
                    'created_at': datetime.now(timezone.utc).isoformat(),
                }).execute()
            except Exception as e:
                logger.error("Failed to store API key: %s", e)
                return None


        return api_key


api_auth = APIAuth()
