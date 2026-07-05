"""
Twitter/X Plugin v1.2
Cross-posts content to Twitter/X via OAuth 2.0 Bearer token.
"""

import logging
import requests
from typing import Any, Optional

from plugins.base import Plugin

logger = logging.getLogger("kanyoza.plugins.twitter")


class TwitterPlugin(Plugin):
    """Publishes posts to Twitter/X via OAuth 2.0."""

    def __init__(self):
        self.access_token = ""
        self.api_base = "https://api.twitter.com/2"
        self._started = False

    @property
    def name(self) -> str:
        return "twitter"

    def configure(self, access_token: str, refresh_token: str = "") -> None:
        """Set credentials after instantiation."""
        self.access_token = access_token

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
        if not self.is_configured:
            return {"ok": False, "platform": "twitter", "reason": "Not configured"}
        try:
            resp = requests.get(
                f"{self.api_base}/users/me",
                headers={"Authorization": f"Bearer {self.access_token}"},
                timeout=10,
            )
            return {
                "ok": resp.status_code == 200,
                "platform": "twitter",
                "status": resp.status_code,
            }
        except Exception as e:
            return {"ok": False, "platform": "twitter", "reason": str(e)[:120]}

    def publish(
        self,
        content: str,
        image_url: Optional[str] = None,
        image_bytes: Optional[bytes] = None,
        **kwargs,
    ) -> dict[str, Any]:
        """Publish a tweet."""
        if not self.is_configured:
            return {"ok": False, "reason": "Not configured"}

        try:
            tweet_text = content[:280]

            resp = requests.post(
                f"{self.api_base}/tweets",
                headers={
                    "Authorization": f"Bearer {self.access_token}",
                    "Content-Type": "application/json",
                },
                json={"text": tweet_text},
                timeout=15,
            )

            if resp.status_code == 201:
                tweet_id = resp.json().get("data", {}).get("id")
                logger.info("✅ Tweet posted: %s", tweet_id)
                return {"ok": True, "platform": "twitter", "tweet_id": tweet_id}

            logger.error("Twitter error: %s %s", resp.status_code, resp.text[:200])
            return {"ok": False, "platform": "twitter", "reason": f"HTTP {resp.status_code}"}

        except Exception as e:
            logger.error("Twitter error: %s", e)
            return {"ok": False, "platform": "twitter", "reason": str(e)[:120]}
