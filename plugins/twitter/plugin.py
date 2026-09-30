"""
Twitter/X Plugin v1.4
Cross-posts content to Twitter/X via OAuth 2.0 Bearer token.
- Auto-refreshes expired tokens
- Threads long posts over 280 characters
"""

import logging
import time
import requests
from typing import Any, Optional

from plugins.base import Plugin

logger = logging.getLogger("kanyoza.plugins.twitter")

# Twitter OAuth 2.0 endpoints
TOKEN_URL = "https://api.twitter.com/2/oauth2/token"


class TwitterPlugin(Plugin):
    """Publishes posts to Twitter/X via OAuth 2.0."""

    def __init__(self, social_repo: "SocialAccountRepository" = None):
        self._social_repo = social_repo
        self.access_token = ""
        self.refresh_token = ""
        self.client_id = ""
        self.client_secret = ""
        self._token_expires_at: float = 0
        self.api_base = "https://api.twitter.com/2"
        self._started = False
        self._connector = None

    @property
    def name(self) -> str:
        return "twitter"

    def configure(
        self,
        access_token: str = "",
        refresh_token: str = "",
        client_id: str = "",
        client_secret: str = "",
    ) -> None:
        """Set credentials after instantiation."""
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.client_id = client_id
        self.client_secret = client_secret
        # Assume token expires in 2 hours from now if no expiry provided
        self._token_expires_at = time.time() + 7200

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

    # ------------------------------------------------------------------
    # Token Refresh
    # ------------------------------------------------------------------

    def _is_token_expired(self) -> bool:
        """Check if access token is expired or will expire within 5 minutes."""
        return time.time() > (self._token_expires_at - 300)

    def _refresh_access_token(self) -> bool:
        """
        Refresh the OAuth 2.0 access token using the refresh token.
        Updates self.access_token, self.refresh_token, and self._token_expires_at.
        """
        if not self.refresh_token:
            logger.warning("No refresh token available — cannot refresh Twitter token")
            return False

        if not self.client_id or not self.client_secret:
            logger.warning("Missing client_id or client_secret — cannot refresh Twitter token")
            return False

        try:
            response = requests.post(
                TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self.refresh_token,
                    "client_id": self.client_id,
                },
                auth=(self.client_id, self.client_secret),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=15,
            )

            if response.status_code != 200:
                logger.error("Token refresh failed: %s — %s", response.status_code, response.text)
                return False

            data = response.json()
            self.access_token = data.get("access_token", self.access_token)
            self.refresh_token = data.get("refresh_token", self.refresh_token)
            self._token_expires_at = time.time() + data.get("expires_in", 7200)

            # Update the underlying connector
            if self._connector:
                self._connector._token = self.access_token

            # Also update in database if we have a repo
            if self._social_repo:
                self._save_refreshed_token()

            logger.info("Twitter token refreshed — expires in %s seconds", data.get("expires_in", 7200))
            return True

        except Exception as e:
            logger.error("Token refresh exception: %s", e)
            return False

    def _save_refreshed_token(self) -> None:
        """Persist the refreshed token to the database."""
        try:
            accounts = self._social_repo.find_by_platform("twitter")
            for account in accounts:
                if account.access_token != self.access_token:
                    account.access_token = self.access_token
                    account.refresh_token = self.refresh_token
                    self._social_repo.save(account)
        except Exception as e:
            logger.warning("Could not persist refreshed Twitter token: %s", e)

    def _ensure_fresh_token(self) -> bool:
        """Ensure we have a valid token before publishing. Returns True if ready."""
        if not self._is_token_expired():
            return True
        return self._refresh_access_token()

    # ------------------------------------------------------------------
    # Thread Publishing
    # ------------------------------------------------------------------

    @staticmethod
    def _split_into_thread(content: str, max_chars: int = 270) -> list[str]:
        """
        Split long content into tweet-sized chunks.
        Leaves room for thread numbering like ' (1/8)'.
        """
        counter_space = 8  # " (12/34)"
        chunks = []
        words = content.split()
        current_chunk = ""

        for word in words:
            test = f"{current_chunk} {word}".strip()
            if len(test) + counter_space <= max_chars:
                current_chunk = test
            else:
                if current_chunk:
                    chunks.append(current_chunk)
                current_chunk = word

        if current_chunk:
            chunks.append(current_chunk)

        # Add thread numbering if multiple chunks
        total = len(chunks)
        if total > 1:
            # First tweet gets "🧵" emoji
            chunks[0] = f"{chunks[0]} 🧵"
            # Number all chunks
            return [f"{chunk} ({i+1}/{total})" for i, chunk in enumerate(chunks)]

        return chunks

    def _publish_thread(
        self,
        connector: Any,
        chunks: list[str],
        image_bytes: Optional[bytes] = None,
    ) -> dict[str, Any]:
        """
        Publish a thread of tweets.
        First tweet stands alone. Subsequent tweets are replies to the first.
        """
        tweet_ids = []

        # Post first tweet (with image if provided)
        first_res = connector.publish(
            chunks[0],
            image_bytes=image_bytes if len(chunks) == 1 else None,
        )
        if not first_res.ok:
            return {
                "ok": False,
                "platform": "twitter",
                "reason": f"First tweet failed: {first_res.error}",
            }

        tweet_ids.append(first_res.post_id)
        parent_id = first_res.post_id

        # Post remaining chunks as replies
        for i, chunk in enumerate(chunks[1:], start=2):
            reply_res = connector.publish(
                chunk,
                reply_to=parent_id,
            )
            if reply_res.ok:
                tweet_ids.append(reply_res.post_id)
                parent_id = reply_res.post_id
                # Small delay to avoid rate limiting
                time.sleep(1.5)
            else:
                logger.warning(
                    "Thread chunk %d/%d failed: %s",
                    i, len(chunks), reply_res.error,
                )
                # A partially published thread is not a successful publish.
                # The first tweet is already externally visible, so the
                # canonical pipeline must treat this as ambiguous and reconcile
                # before any automatic retry can create another thread.
                return {
                    "ok": False,
                    "platform": "twitter",
                    "post_id": tweet_ids[0] if tweet_ids else None,
                    "tweet_ids": tweet_ids,
                    "thread_count": len(tweet_ids),
                    "reason": f"Thread partially published at chunk {i}/{len(chunks)}: {reply_res.error}",
                    "outcome": "uncertain",
                    "reconciliation_required": True,
                }

        return {
            "ok": True,
            "platform": "twitter",
            "post_id": tweet_ids[0] if tweet_ids else "",
            "tweet_ids": tweet_ids,
            "thread_count": len(tweet_ids),
            "reason": None,
            "outcome": "succeeded",
        }

    # ------------------------------------------------------------------
    # Publish
    # ------------------------------------------------------------------

    def publish(
        self,
        content: str,
        image_url: Optional[str] = None,
        image_bytes: Optional[bytes] = None,
        brand: Optional[Any] = None,
        **kwargs,
    ) -> dict[str, Any]:
        """Publish a tweet with auto-refresh and thread support."""
        if not self._started:
            return {"ok": False, "platform": "twitter", "reason": "Not started"}

        # Ensure token is fresh before publishing
        if not self._ensure_fresh_token():
            return {"ok": False, "platform": "twitter", "reason": "Token expired — refresh failed"}

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

        # Never silently discard media. The connector can upload raw bytes; a URL
        # alone cannot be safely fetched here without an explicit media-ingest
        # policy, so reject it deterministically rather than publishing text-only.
        if image_url and not image_bytes:
            return {
                "ok": False,
                "platform": "twitter",
                "reason": "Twitter media requires image_bytes; image_url alone is not silently ignored",
                "outcome": "failed",
            }

        # Check if content needs threading (over 280 chars)
        content_stripped = content.strip()
        if len(content_stripped) > 280:
            logger.info(
                "Content is %d chars — splitting into thread", len(content_stripped)
            )
            chunks = self._split_into_thread(content_stripped)
            logger.info("Split into %d chunks", len(chunks))
            return self._publish_thread(connector, chunks, image_bytes)

        # Normal single tweet
        res = connector.publish(content, image_bytes=image_bytes, image_url=image_url)
        return {
            "ok": res.ok,
            "platform": "twitter",
            "post_id": res.post_id,
            "tweet_id": res.post_id,
            "reason": res.error,
        }
