"""
Twitter/X Plugin v1.3
Cross-posts content to Twitter/X via OAuth 2.0 Bearer token.
Smart truncation for 280-character limit.
"""

import logging
import requests
from typing import Any, Optional

from plugins.base import Plugin

logger = logging.getLogger("kanyoza.plugins.twitter")


class TwitterPlugin(Plugin):
    """Publishes posts to Twitter/X via OAuth 2.0."""

    def __init__(self, social_repo: "SocialAccountRepository" = None):
        self._social_repo = social_repo
        self.access_token = ""
        self.api_base = "https://api.twitter.com/2"
        self._started = False
        self._connector = None

    @property
    def name(self) -> str:
        return "twitter"

    def configure(self, access_token: str, refresh_token: str = "") -> None:
        """Set credentials after instantiation."""
        self.access_token = access_token
        from services.connectors.twitter_connector import TwitterConnector
        self._connector = TwitterConnector()
        self._connector._token = access_token

    @property
    def is_configured(self) -> bool:
        return bool(self.access_token)

    def start(self) -> None:
        self._started = True
        logger.info("TwitterPlugin started (configured=%s)", self.is_configured)

    def stop(self) -> None:
        self._started = False
        logger.info("TwitterPlugin stopped")

    def health(self) -> dict[str, Any]:
        if not self._connector:
            return {"ok": False, "platform": "twitter", "reason": "Not configured"}
        h = self._connector.health()
        return {
            "ok": h.ok,
            "platform": "twitter",
            "reason": h.error,
        }

    @property
    def connector(self) -> Optional[Any]:
        return self._connector

    def publish(
        self,
        content: str,
        image_url: Optional[str] = None,
        image_bytes: Optional[bytes] = None,
        brand: Optional[Any] = None,
        **kwargs,
    ) -> dict[str, Any]:
        """Publish a tweet with smart truncation for 280-char limit."""
        if not self._started:
            return {"ok": False, "platform": "twitter", "reason": "Not started"}

        connector = self._connector
        if brand and self._social_repo:
            try:
                account = self._social_repo.find_by_brand_and_platform(brand.id, "twitter")
                if account:
                    from services.connectors.connector_factory import create_connector
                    connector = create_connector(account)
            except Exception as e:
                logger.error("Failed to load Twitter connector for brand %s: %s", brand.name, e)

        if not connector:
            return {"ok": False, "platform": "twitter", "reason": "Not configured"}

        res = connector.publish(content, image_bytes=image_bytes, image_url=image_url)
        return {
            "ok": res.ok,
            "platform": "twitter",
            "tweet_id": res.post_id,
            "reason": res.error,
        }
