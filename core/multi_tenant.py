"""
Multi-Tenant Engine v1.0
- Manage multiple Facebook pages from one bot instance
- Each tenant has isolated data
- Tenant-aware scheduling, posting, and engagement
"""

import logging
from typing import Dict, List, Optional
from datetime import datetime

logger = logging.getLogger("kanyoza.core.multi_tenant")


class Tenant:
    """A single customer/account with their own Facebook page."""

    def __init__(self, data: dict):
        self.id = data.get('id')
        self.name = data.get('name', '')
        self.page_id = data.get('page_id', '')
        self.page_access_token = data.get('page_access_token', '')
        self.verify_token = data.get('verify_token', '')
        self.owner_psid = data.get('owner_psid', '')
        self.plan = data.get('plan', 'free')  # free, pro, business, enterprise
        self.status = data.get('status', 'active')
        self.content_hours = data.get('content_hours_utc', [4, 7, 10, 14, 17, 19])
        self.news_hours = data.get('news_hours_utc', [6, 13, 18])
        self.gemini_model = data.get('gemini_model', 'gemini-2.5-flash')
        self.features = data.get('features', {
            'auto_reply': True,
            'auto_post': True,
            'auto_comment_reply': True,
            'cross_engage': False,
            'guardian': False,
            'thumbnails': False,
        })
        self.created_at = data.get('created_at', datetime.now().isoformat())
        self.metadata = data.get('metadata', {})


class MultiTenantEngine:
    """
    Manages multiple tenants (Facebook pages) from one bot.
    Each tenant has isolated configuration and state.
    """

    def __init__(self, supabase_client=None):
        self.supabase = supabase_client
        self._tenants: Dict[str, Tenant] = {}
        self._active_tenant_id: Optional[str] = None
        self._load_tenants()

    def _load_tenants(self):
        """Load all tenants from database."""
        if not self.supabase:
            return
        try:
            result = self.supabase.table('tenants').select('*').eq('status', 'active').execute()
            if result.data:
                for row in result.data:
                    tenant = Tenant(row)
                    self._tenants[tenant.id] = tenant
                logger.info(f"Loaded {len(self._tenants)} tenants")
        except Exception as e:
            logger.error(f"Failed to load tenants: {e}")

    def get_tenant(self, tenant_id: str) -> Optional[Tenant]:
        """Get a specific tenant."""
        return self._tenants.get(tenant_id)

    def get_all_tenants(self) -> List[Tenant]:
        """Get all active tenants."""
        return list(self._tenants.values())

    def get_tenant_by_page_id(self, page_id: str) -> Optional[Tenant]:
        """Find tenant by Facebook page ID."""
        for tenant in self._tenants.values():
            if tenant.page_id == page_id:
                return tenant
        return None

    def add_tenant(self, data: dict) -> Optional[Tenant]:
        """Register a new tenant."""
        if not self.supabase:
            return None
        try:
            result = self.supabase.table('tenants').insert({
                'id': data.get('id'),
                'name': data['name'],
                'page_id': data['page_id'],
                'page_access_token': data['page_access_token'],
                'verify_token': data.get('verify_token', ''),
                'owner_psid': data.get('owner_psid', ''),
                'plan': data.get('plan', 'free'),
                'status': 'active',
                'features': data.get('features', {}),
                'content_hours_utc': data.get('content_hours_utc', [4, 7, 10, 14, 17, 19]),
                'news_hours_utc': data.get('news_hours_utc', [6, 13, 18]),
                'gemini_model': data.get('gemini_model', 'gemini-2.5-flash'),
                'created_at': datetime.now().isoformat(),
            }).execute()
            if result.data:
                tenant = Tenant(result.data[0])
                self._tenants[tenant.id] = tenant
                return tenant
        except Exception as e:
            logger.error(f"Failed to add tenant: {e}")
        return None

    def update_tenant(self, tenant_id: str, updates: dict) -> bool:
        """Update tenant configuration."""
        if not self.supabase:
            return False
        try:
            self.supabase.table('tenants').update(updates).eq('id', tenant_id).execute()
            if tenant_id in self._tenants:
                for key, value in updates.items():
                    setattr(self._tenants[tenant_id], key, value)
            return True
        except Exception as e:
            logger.error(f"Failed to update tenant: {e}")
            return False

    def disable_tenant(self, tenant_id: str) -> bool:
        """Disable a tenant (non-destructive)."""
        return self.update_tenant(tenant_id, {'status': 'disabled'})

    def check_feature(self, tenant_id: str, feature: str) -> bool:
        """Check if a feature is enabled for a tenant."""
        tenant = self.get_tenant(tenant_id)
        if not tenant:
            return False
        return tenant.features.get(feature, False)

    def get_tenant_count(self) -> dict:
        """Get tenant statistics."""
        counts = {'free': 0, 'pro': 0, 'business': 0, 'enterprise': 0, 'total': 0}
        for tenant in self._tenants.values():
            plan = tenant.plan if tenant.plan in counts else 'free'
            counts[plan] += 1
            counts['total'] += 1
        return counts

    def is_tenant_active(self, tenant_id: str) -> bool:
        """Check if tenant is active."""
        tenant = self.get_tenant(tenant_id)
        return tenant is not None and tenant.status == 'active'


# Singleton
tenants = MultiTenantEngine()
