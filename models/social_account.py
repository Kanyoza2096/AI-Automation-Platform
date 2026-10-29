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

    def to_public_dict(self) -> Dict[str, Any]:
        """Safe projection for external API serialization.

        CRITICAL: the ONLY method to serialize a SocialAccount for an
        external caller. Never use .dict(), .model_dump(), vars(), or
        __dict__ — the model carries credential fields that are decrypted
        at read time and must not leave the process boundary.

        Excluded by design:
          - access_token, refresh_token, app_secret (OAuth credentials)
          - platform_config (may contain verify_token and other secrets)
          - metadata (free-form; treated as untrusted for external APIs)

        Includes token_expires_at — that's metadata about the credential
        (useful to the frontend for "reconnect in N days" UX), not the
        credential itself.

        See INCIDENT-2026-10-16 for the class of bug this guards against.
        """
        return {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "brand_id": self.brand_id,
            "platform": (
                self.platform.value
                if isinstance(self.platform, Platform)
                else str(self.platform)
            ),
            "platform_account_id": self.platform_account_id,
            "account_name": self.account_name,
            "enabled": self.enabled,
            "health_status": (
                self.health_status.value
                if isinstance(self.health_status, AccountHealthStatus)
                else str(self.health_status)
            ),
            "timezone": self.timezone,
            "posting_schedule": self.posting_schedule,
            "preferred_content_types": self.preferred_content_types,
            "token_expires_at": (
                self.token_expires_at.isoformat() if self.token_expires_at else None
            ),
            "last_health_check": (
                self.last_health_check.isoformat() if self.last_health_check else None
            ),
            "last_publish_at": (
                self.last_publish_at.isoformat() if self.last_publish_at else None
            ),
            "consecutive_failures": self.consecutive_failures,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

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
