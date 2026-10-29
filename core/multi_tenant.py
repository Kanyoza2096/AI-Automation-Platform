"""
Multi-Tenant Engine v1.0 (Deprecated -> Migrating to WorkspaceManager v2.0)
- Manage multiple Facebook pages from one bot instance
- Each tenant has isolated data
- Tenant-aware scheduling, posting, and engagement
"""

import logging
from typing import Dict, List, Optional
from datetime import datetime
import warnings

from models.workspace import Workspace, WorkspacePlan, WorkspaceStatus
from repositories.workspace_repository import WorkspaceRepository

logger = logging.getLogger("kanyoza.core.multi_tenant")


class Tenant:
    """
    [DEPRECATED] A single customer/account with their own Facebook page.
    This is now a backwards-compatibility shim around the Workspace model.
    """

    def __init__(self, data: dict):
        warnings.warn("Tenant class is deprecated. Use Workspace model instead.", DeprecationWarning, stacklevel=2)
        # Delegate to Workspace for core attributes
        self._workspace = Workspace(
            id=data.get('id', 'default'),
            name=data.get('name', 'Default Workspace'),
            plan=WorkspacePlan(data.get('plan', 'free')),
            status=WorkspaceStatus(data.get('status', 'active')),
            settings=data.get('features', {}),
            created_at=datetime.fromisoformat(data.get('created_at')) if data.get('created_at') else datetime.now(),
        )
        
        # Keep old attributes for compatibility until Phase 3/4 completes
        self.page_id = data.get('page_id', '')
        self.page_access_token = data.get('page_access_token', '')
        self.verify_token = data.get('verify_token', '')
        self.owner_psid = data.get('owner_psid', '')
        self.content_hours = data.get('content_hours_utc', [4, 7, 10, 14, 17, 19])
        self.news_hours = data.get('news_hours_utc', [6, 13, 18])
        self.gemini_model = data.get('gemini_model', 'gemini-2.5-flash')
        self.metadata = data.get('metadata', {})
        
    @property
    def id(self) -> str:
        return self._workspace.id

    @property
    def name(self) -> str:
        return self._workspace.name

    @property
    def plan(self) -> str:
        return self._workspace.plan.value

    @property
    def status(self) -> str:
        return self._workspace.status.value

    @property
    def features(self) -> dict:
        return self._workspace.settings

    @property
    def created_at(self) -> str:
        return self._workspace.created_at.isoformat()


class WorkspaceManager:
    """
    Manages workspaces (formerly tenants).
    Wraps the WorkspaceRepository to provide caching and helper methods.
    """

    def __init__(self, workspace_repository: WorkspaceRepository):
        self.repository = workspace_repository

    def get_workspace(self, workspace_id: str) -> Optional[Workspace]:
        """Get a specific workspace."""
        return self.repository.find_by_id(workspace_id)

    def get_all_workspaces(self) -> List[Workspace]:
        """Get all workspaces."""
        return self.repository.find_all()

    def add_workspace(self, data: dict) -> Optional[Workspace]:
        """Register a new workspace."""
        try:
            workspace = Workspace(
                id=data.get('id'),
                name=data['name'],
                owner_email=data.get('owner_email', ''),
                plan=WorkspacePlan(data.get('plan', 'free')),
                status=WorkspaceStatus.ACTIVE,
                settings=data.get('features', {})
            )
            return self.repository.save(workspace)
        except Exception as e:
            logger.error("Failed to add workspace: %s", e)
            return None

    def update_workspace(self, workspace_id: str, updates: dict) -> bool:
        """Update workspace configuration."""
        workspace = self.repository.find_by_id(workspace_id)
        if not workspace:
            return False
            
        try:
            # Apply updates
            for key, value in updates.items():
                if hasattr(workspace, key):
                    setattr(workspace, key, value)
            self.repository.save(workspace)
            return True
        except Exception as e:
            logger.error("Failed to update workspace: %s", e)
            return False

    def disable_workspace(self, workspace_id: str) -> bool:
        """Disable a workspace (non-destructive)."""
        return self.update_workspace(workspace_id, {'status': WorkspaceStatus.SUSPENDED})

    def check_feature(self, workspace_id: str, feature: str) -> bool:
        """Check if a feature is enabled for a workspace."""
        workspace = self.get_workspace(workspace_id)
        if not workspace:
            return False
        return workspace.settings.get(feature, False)

    def is_workspace_active(self, workspace_id: str) -> bool:
        """Check if workspace is active."""
        workspace = self.get_workspace(workspace_id)
        return workspace is not None and workspace.is_active


# Backwards compatibility: Aliasing WorkspaceManager to MultiTenantEngine
class MultiTenantEngine(WorkspaceManager):
    """
    [DEPRECATED] Shim for old MultiTenantEngine. 
    Use WorkspaceManager instead.
    """
    def __init__(self, supabase_client=None):
        warnings.warn("MultiTenantEngine is deprecated. Use WorkspaceManager via Dependency Injection.", DeprecationWarning, stacklevel=2)
        repo = WorkspaceRepository(supabase_client)
        super().__init__(repo)
        
    def _load_tenants(self):
        """No-op. Repository handles loading."""
        pass
        
    def get_tenant(self, tenant_id: str) -> Optional[Tenant]:
        ws = self.get_workspace(tenant_id)
        if not ws: return None
        return Tenant(ws.model_dump())
        
    def get_all_tenants(self) -> List[Tenant]:
        return [Tenant(ws.model_dump()) for ws in self.get_all_workspaces()]
        
    def get_tenant_by_page_id(self, page_id: str) -> Optional[Tenant]:
        # This is a temporary hack since Workspace doesn't have page_id. 
        # For full compat, we should look it up from SocialAccountRepository,
        # but for this shim, we'll return None and rely on the new systems.
        warnings.warn("get_tenant_by_page_id is not supported in the shim.", DeprecationWarning)
        return None
        
    def add_tenant(self, data: dict) -> Optional[Tenant]:
        ws = self.add_workspace(data)
        if not ws: return None
        return Tenant(ws.model_dump())
        
    def update_tenant(self, tenant_id: str, updates: dict) -> bool:
        return self.update_workspace(tenant_id, updates)
        
    def disable_tenant(self, tenant_id: str) -> bool:
        return self.disable_workspace(tenant_id)

# Singleton shim for backwards compatibility
tenants = MultiTenantEngine()
