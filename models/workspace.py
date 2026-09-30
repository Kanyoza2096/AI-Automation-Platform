"""
Workspace domain models — multi-tenant isolation root.

A Workspace is the top-level organisational unit. Every database record
(brands, social accounts, knowledge entries, analytics events, etc.)
belongs to exactly one workspace.

WorkspaceMember tracks who has access to a workspace and their role.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import Field, field_validator

from models.base import KanyozaModel


class WorkspacePlan(str, Enum):
    """Billing plan tier for a workspace."""
    FREE = "free"
    PRO = "pro"
    BUSINESS = "business"
    ENTERPRISE = "enterprise"


class WorkspaceStatus(str, Enum):
    """Lifecycle status of a workspace."""
    ACTIVE = "active"
    SUSPENDED = "suspended"
    ARCHIVED = "archived"


class MemberRole(str, Enum):
    """Role hierarchy: owner > admin > editor > viewer."""
    OWNER = "owner"
    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"

    @property
    def level(self) -> int:
        """Numeric level for comparison (higher = more permissions)."""
        return {"owner": 40, "admin": 30, "editor": 20, "viewer": 10}[self.value]

    def has_at_least(self, required: "MemberRole") -> bool:
        """Return True if this role meets or exceeds *required*."""
        return self.level >= required.level


class Workspace(KanyozaModel):
    """
    Top-level tenant entity.

    All data in the platform is scoped to a workspace. A single deployment
    can host unlimited workspaces, each fully isolated.
    """

    id: str = Field(..., min_length=1, description="Unique workspace identifier (UUID or slug)")
    name: str = Field(..., min_length=1, max_length=200, description="Human-readable workspace name")
    slug: str = Field(..., min_length=1, max_length=100, description="URL-safe identifier")
    owner_email: str = Field(default="", description="Primary owner email")
    plan: WorkspacePlan = Field(default=WorkspacePlan.FREE)
    status: WorkspaceStatus = Field(default=WorkspaceStatus.ACTIVE)

    # Workspace-level settings (JSON blob for extensibility)
    settings: Dict[str, Any] = Field(default_factory=dict, description="Workspace-level config overrides")

    # Limits (driven by plan)
    max_brands: int = Field(default=5, ge=1, description="Maximum number of brands allowed")
    max_social_accounts: int = Field(default=10, ge=1, description="Maximum connected social accounts")
    max_posts_per_day: int = Field(default=50, ge=1, description="Global daily post limit across all brands")

    # Metadata
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)
    updated_at: Optional[datetime] = Field(default=None)

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, v: str) -> str:
        """Ensure slug is URL-safe: lowercase alphanumeric + hyphens only."""
        import re
        cleaned = re.sub(r"[^a-z0-9-]", "", v.lower())
        if not cleaned:
            raise ValueError("slug must contain at least one alphanumeric character")
        return cleaned

    @property
    def is_active(self) -> bool:
        return self.status == WorkspaceStatus.ACTIVE

    @classmethod
    def create_default(cls, workspace_id: str = "default") -> "Workspace":
        """Create a default workspace for single-tenant backwards compatibility."""
        return cls(
            id=workspace_id,
            name="Default Workspace",
            slug="default",
            owner_email="",
            plan=WorkspacePlan.FREE,
            status=WorkspaceStatus.ACTIVE,
            settings={},
        )


class WorkspaceMember(KanyozaModel):
    """
    Tracks a user's membership and role within a workspace.
    """

    id: str = Field(..., description="Unique membership ID")
    workspace_id: str = Field(..., description="Owning workspace")
    user_id: str = Field(..., description="User identifier (email or auth UID)")
    role: MemberRole = Field(default=MemberRole.VIEWER)
    invited_at: datetime = Field(default_factory=KanyozaModel.utcnow)
    accepted_at: Optional[datetime] = Field(default=None)

    @property
    def is_admin_or_above(self) -> bool:
        return self.role.has_at_least(MemberRole.ADMIN)
