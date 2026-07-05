"""
Twitter/X Plugin v1.1 — OAuth 2.0
Cross-posts content to Twitter/X using Bearer token.
"""

import logging
import requests
from typing import Optional

from plugins.base import Plugin, PluginConfig

logger = logging.getLogger("kanyoza.plugins.twitter")


class TwitterPlugin(Plugin):
    """Publishes posts to Twitter/X via OAuth 2.0."""

    def __init__(self, config: PluginConfig):
        super().__init__(config)
        self.access_token = config.settings.get("access_token", "")
        self.api_base = "https://api.twitter.com/2"

    @property
    def is_configured(self) -> bool:
        return bool(self.access_token)

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    def health(self) -> dict:
        if not self.is_configured:
            return {"ok": False, "reason": "Not configured"}
        try:
            resp = requests.get(
                f"{self.api_base}/users/me",
                headers=self._headers(),
                timeout=10,
            )
            return {"ok": resp.status_code == 200}
        except Exception as e:
            return {"ok": False, "reason": str(e)[:120]}

    def publish(self, content: str, image_bytes: Optional[bytes] = None) -> dict:
        """Publish a tweet."""
        if not self.is_configured:
            return {"ok": False, "reason": "Not configured"}

        try:
            tweet_text = content[:280]

            resp = requests.post(
                f"{self.api_base}/tweets",
                headers=self._headers(),
                json={"text": tweet_text},
                timeout=15,
            )

            if resp.status_code == 201:
                tweet_id = resp.json().get("data", {}).get("id")
                logger.info(f"✅ Tweet posted: {tweet_id}")
                return {"ok": True, "tweet_id": tweet_id}

            logger.error(f"Twitter error: {resp.status_code} {resp.text[:200]}")
            return {"ok": False, "reason": f"HTTP {resp.status_code}"}

        except Exception as e:
            logger.error(f"Twitter error: {e}")
            return {"ok": False, "reason": str(e)[:120]}
