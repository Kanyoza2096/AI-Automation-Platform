"""
Social Account domain model — a connected social media account.

Each social account represents a single connection to a platform:
  - A Facebook Page
  - An Instagram Business Account
  - A LinkedIn Company Page
  - A Twitter/X account
  - A Telegram Bot/Channel
  - A WhatsApp Business number

Social accounts belong to a workspace and can optionally be linked
to a specific brand. They store platform-specific credentials,
scheduling preferences, and health status.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import Field, field_validator

from models.base import KanyozaModel


class Platform(str, Enum):
    """Supported social media platforms."""
    FACEBOOK = "facebook"
    INSTAGRAM = "instagram"
    LINKEDIN = "linkedin"
    TWITTER = "twitter"
    TELEGRAM = "telegram"
    WHATSAPP = "whatsapp"
    WEBSITE = "website"
    TIKTOK = "tiktok"


class AccountHealthStatus(str, Enum):
    """Health status of a social account connection."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class SocialAccount(KanyozaModel):
    """
    A single connected social media account.

    Stores platform credentials (encrypted at rest), scheduling
    preferences, and health status independently.

    Example:
        Workspace: Kanyoza Systems
        Accounts:
          - Python Daily (Facebook Page)
          - AI Malawi (Facebook Page)
          - Cloud Engineering (LinkedIn)
          - Backend Academy (Twitter)
    """

    id: str = Field(..., min_length=1, description="Unique account identifier")
    workspace_id: str = Field(..., min_length=1, description="Owning workspace")
    brand_id: Optional[str] = Field(
        default=None,
        description="Linked brand (None = unassigned, available for any brand)",
    )

    # Platform identity
    platform: Platform = Field(..., description="Social media platform")
    platform_account_id: str = Field(
        default="",
        description="Platform-specific account/page ID",
    )
    account_name: str = Field(default="", description="Display name on the platform")

    # Credentials (stored encrypted; decrypted at runtime)
    access_token: str = Field(default="", description="OAuth access token (encrypted at rest)")
    refresh_token: str = Field(default="", description="OAuth refresh token (encrypted at rest)")
    token_expires_at: Optional[datetime] = Field(default=None, description="Token expiration time")
    app_secret: str = Field(default="", description="Platform app secret (encrypted at rest)")

    # Platform-specific configuration
    platform_config: Dict[str, Any] = Field(
        default_factory=dict,
        description="Platform-specific settings (e.g., verify_token for Facebook webhooks)",
    )

    # Scheduling
    timezone: str = Field(default="UTC", max_length=50)
    posting_schedule: Dict[str, Any] = Field(
        default_factory=lambda: {
            "content_hours": [1, 3, 5, 7, 9, 11, 13, 15, 17, 19],
            "max_posts_per_day": 10,
        },
        description="Account-specific posting schedule",
    )
    preferred_content_types: List[str] = Field(
        default_factory=lambda: ["image", "text"],
        description="Preferred content formats: image, text, carousel, video",
    )

    # State
    enabled: bool = Field(default=True, description="Whether this account is active for publishing")
    health_status: AccountHealthStatus = Field(default=AccountHealthStatus.UNKNOWN)
    last_health_check: Optional[datetime] = Field(default=None)
    last_publish_at: Optional[datetime] = Field(default=None)
    consecutive_failures: int = Field(default=0, ge=0)

    # Metadata
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)
    updated_at: Optional[datetime] = Field(default=None)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        """Account is usable for publishing."""
        return self.enabled and self.health_status != AccountHealthStatus.UNHEALTHY

    @property
    def is_token_expired(self) -> bool:
        """Check if the access token has expired."""
        if self.token_expires_at is None:
            return False  # Assume valid if no expiry set
        return datetime.now(self.token_expires_at.tzinfo or None) > self.token_expires_at

    @property
    def needs_health_check(self) -> bool:
        """True if a health check hasn't been run in the last hour."""
        if self.last_health_check is None:
            return True
        from datetime import timedelta, timezone as tz
        return (datetime.now(tz.utc) - self.last_health_check) > timedelta(hours=1)

    @property
    def content_hours(self) -> List[int]:
        return self.posting_schedule.get("content_hours", [])

    @classmethod
    def create_from_env(
        cls,
        workspace_id: str,
        account_id: str = "default-facebook",
        page_access_token: str = "",
        page_id: str = "",
        verify_token: str = "",
        app_secret: str = "",
    ) -> "SocialAccount":
        """
        Create a SocialAccount from legacy environment variables.

        Used for backwards compatibility: existing single-page deployments
        automatically create a default Facebook social account from their
        PAGE_ACCESS_TOKEN, PAGE_ID, etc.
        """
        return cls(
            id=account_id,
            workspace_id=workspace_id,
            platform=Platform.FACEBOOK,
            platform_account_id=page_id,
            account_name="Default Facebook Page",
            access_token=page_access_token,
            app_secret=app_secret,
            platform_config={
                "verify_token": verify_token,
            },
        )
