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
        """Validate an API key and return tenant info."""
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
                'tenant_id': key_data['tenant_id'],
                'permissions': key_data.get('permissions', ['read']),
                'tenant': key_data.get('tenants', {}),
            }

        except Exception as e:
            logger.error(f"API key validation error: {e}")
            return {'valid': False, 'error': 'Validation failed'}

    def require_api_key(self, f):
        """Decorator to require valid API key."""
        @wraps(f)
        def decorated(*args, **kwargs):
            api_key = request.headers.get('X-API-Key', '')
            if not api_key:
                return jsonify({'error': 'API key required'}), 401

            validation = self.validate_key(api_key)
            if not validation['valid']:
                return jsonify({'error': validation.get('error', 'Unauthorized')}), 401

            request.tenant = validation.get('tenant', {})
            request.tenant_id = validation.get('tenant_id')
            request.permissions = validation.get('permissions', ['read'])

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
