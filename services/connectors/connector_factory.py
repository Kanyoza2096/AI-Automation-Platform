"""
Connector Factory — creates the right SocialConnector for a given SocialAccount.

Usage:
    from services.connectors.connector_factory import create_connector
    connector = create_connector(social_account)
    result = connector.publish("Hello world!")
"""

from __future__ import annotations

import logging
from typing import Optional, TYPE_CHECKING

from services.connectors.connector import SocialConnector, Platform

if TYPE_CHECKING:
    from models.social_account import SocialAccount
    from services.facebook import FacebookService

logger = logging.getLogger("kanyoza.connectors.factory")

# Registry mapping platform name → connector class
_CONNECTOR_CLASSES = {}


def _ensure_registry():
    """Lazily populate the connector class registry to avoid import cycles."""
    if _CONNECTOR_CLASSES:
        return

    from services.connectors.facebook_connector import FacebookConnector
    from services.connectors.twitter_connector import TwitterConnector
    from services.connectors.linkedin_connector import LinkedInConnector
    from services.connectors.instagram_connector import InstagramConnector
    from services.connectors.telegram_connector import TelegramConnector
    from services.connectors.whatsapp_connector import WhatsAppConnector

    _CONNECTOR_CLASSES.update({
        Platform.FACEBOOK.value: FacebookConnector,
        Platform.TWITTER.value: TwitterConnector,
        Platform.LINKEDIN.value: LinkedInConnector,
        Platform.INSTAGRAM.value: InstagramConnector,
        Platform.TELEGRAM.value: TelegramConnector,
        Platform.WHATSAPP.value: WhatsAppConnector,
    })


def create_connector(
    social_account: "SocialAccount",
    facebook_service: Optional["FacebookService"] = None,
) -> SocialConnector:
    """Create the appropriate connector for a social account.

    Args:
        social_account: The SocialAccount model instance with platform,
                        credentials, and provider_account_id.
        facebook_service: Optional FacebookService instance for Facebook
                          connector (shared HTTP session).

    Returns:
        A SocialConnector implementation bound to the account's credentials.

    Raises:
        ValueError: If the platform is not supported.
    """
    _ensure_registry()

    platform = social_account.platform.lower()
    cls = _CONNECTOR_CLASSES.get(platform)

    if cls is None:
        raise ValueError(
            f"Unsupported platform '{platform}'. "
            f"Supported: {list(_CONNECTOR_CLASSES.keys())}"
        )

    # FacebookConnector needs the shared FacebookService for its HTTP session
    if platform == Platform.FACEBOOK.value:
        if facebook_service is None:
            from services.facebook import facebook as default_fb
            facebook_service = default_fb
        return cls(facebook_service=facebook_service, social_account=social_account)

    return cls(social_account=social_account)


def supported_platforms() -> list[str]:
    """Return list of supported platform names."""
    _ensure_registry()
    return list(_CONNECTOR_CLASSES.keys())


def create_governed_connector(
    social_account: "SocialAccount",
    *,
    workspace_id: str,
    ledger,
    facebook_service: Optional["FacebookService"] = None,
):
    """Create a connector whose mutating operations use the execution ledger."""
    from services.connectors.governed_connector import GovernedConnector
    connector = create_connector(social_account, facebook_service=facebook_service)
    return GovernedConnector(connector, workspace_id=workspace_id, ledger=ledger)
