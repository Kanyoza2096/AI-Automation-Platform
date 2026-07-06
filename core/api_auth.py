"""
API Authentication v1.0
- API key validation
- Rate limit per key
- Permission checking
"""

import hashlib
import logging
from functools import wraps
from datetime import datetime, timezone
from flask import request, jsonify

logger = logging.getLogger("kanyoza.core.api_auth")


class APIAuth:
    """API key authentication manager."""

    def __init__(self, supabase_client=None):
        self.supabase = supabase_client

    def validate_key(self, api_key: str) -> dict:
        """Validate an API key or token, and return workspace / permissions info."""
        from config.settings import settings
        
        # 1. Master API token check
        if settings.master_api_token and api_key == settings.master_api_token:
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
            logger.error(f"Workspace API token validation error: {e}")

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
            logger.error(f"Legacy API key validation error: {e}")
            return {'valid': False, 'error': 'Validation failed'}

    def require_api_key(self, f):
        """Decorator to require valid API key or token, and set workspace context."""
        @wraps(f)
        def decorated(*args, **kwargs):
            # Support both X-API-Key and Authorization: Bearer <token>
            api_key = request.headers.get('X-API-Key', '')
            if not api_key and request.headers.get('Authorization', '').startswith('Bearer '):
                api_key = request.headers.get('Authorization')[7:]

            if not api_key:
                return jsonify({'error': 'API key required'}), 401

            validation = self.validate_key(api_key)
            if not validation['valid']:
                return jsonify({'error': validation.get('error', 'Unauthorized')}), 401

            # Store on request context
            request.workspace_id = validation.get('workspace_id')
            request.permissions = validation.get('permissions', ['read'])
            
            # Propagate to WorkspaceContext
            from core.workspace_context import set_workspace_context
            set_workspace_context(workspace_id=request.workspace_id)

            return f(*args, **kwargs)
        return decorated

    def require_permission(self, permission: str):
        """Decorator to require specific permission."""
        def decorator(f):
            @wraps(f)
            def decorated(*args, **kwargs):
                if not hasattr(request, 'permissions'):
                    return jsonify({'error': 'Authentication required'}), 401
                if permission not in request.permissions:
                    return jsonify({'error': f'Missing permission: {permission}'}), 403
                return f(*args, **kwargs)
            return decorated
        return decorator

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
                logger.error(f"Failed to store API key: {e}")
                return None

        return api_key


api_auth = APIAuth()
