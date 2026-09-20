"""
Social Connector Protocol and shared types.

Defines the unified interface that all platform connectors implement.
This replaces the ad-hoc Plugin.publish() signatures with a typed,
consistent contract that supports publish, delete, edit, health, and
message retrieval across all platforms.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

logger = logging.getLogger("kanyoza.connectors")


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------

class Platform(str, Enum):
    FACEBOOK = "facebook"
    INSTAGRAM = "instagram"
    TWITTER = "twitter"
    LINKEDIN = "linkedin"
    TELEGRAM = "telegram"
    WHATSAPP = "whatsapp"
    TIKTOK = "tiktok"
    WEBSITE = "website"


@dataclass(frozen=True)
class ConnectorResult:
    """Standard return value from any connector publish/edit/delete call."""
    ok: bool
    platform: str
    post_id: Optional[str] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    # failed = provider definitively rejected/invalid request; uncertain =
    # transport or provider ambiguity where the side effect may have happened.
    outcome: str = "unknown"


@dataclass(frozen=True)
class ConnectorHealth:
    """Health probe result."""
    ok: bool
    platform: str
    latency_ms: Optional[float] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    # failed = provider definitively rejected/invalid request; uncertain =
    # transport or provider ambiguity where the side effect may have happened.
    outcome: str = "unknown"


@dataclass
class Comment:
    """Normalised comment across platforms."""
    id: str
    post_id: str
    author_id: str
    author_name: str
    text: str
    created_at: Optional[str] = None
    platform: str = ""


@dataclass
class Message:
    """Normalised DM / inbox message across platforms."""
    id: str
    sender_id: str
    sender_name: str
    text: str
    created_at: Optional[str] = None
    platform: str = ""


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class SocialConnector(Protocol):
    """Unified interface for external publication/connectivity providers.

    Each connector wraps a provider API and exposes a consistent surface.
    Connectors are stateless with respect to the application; they receive
    credentials from a ``SocialAccount`` model at construction time.
    """

    platform: str

    def publish(
        self,
        content: str,
        image_bytes: Optional[bytes] = None,
        image_url: Optional[str] = None,
        **kwargs: Any,
    ) -> ConnectorResult:
        """Publish content. Returns a ConnectorResult."""
        ...

    def lookup_post(self, post_id: str) -> Any:
        """Authoritatively determine whether a provider object exists.

        Return True/False only when the provider response is conclusive;
        otherwise return None. Implementations must fail closed.
        """
        ...

    def delete(self, post_id: str) -> ConnectorResult:
        """Delete a previously published post."""
        ...

    def edit(self, post_id: str, content: str) -> ConnectorResult:
        """Edit a previously published post."""
        ...

    def health(self) -> ConnectorHealth:
        """Run a lightweight health probe against the platform API."""
        ...

    def validate(self) -> bool:
        """Validate that the stored credentials are still valid."""
        ...

    def fetch_comments(self, post_id: str, limit: int = 50) -> List[Comment]:
        """Fetch comments on a specific post."""
        ...

    def fetch_messages(self, limit: int = 50) -> List[Message]:
        """Fetch incoming DMs / inbox messages."""
        ...
