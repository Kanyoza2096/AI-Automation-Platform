"""
Twitter/X Integration Service v1.0
- Cross-post content to Twitter/X
- OAuth 1.0a authentication
- Media upload support
- Rate limit aware
"""

import logging
from requests_oauthlib import OAuth1
from typing import Optional

from config.settings import settings
from utils.http_client import HttpClient

logger = logging.getLogger("kanyoza.services.twitter")

# Shared HTTP client — OAuth1 auth passed per-request
_http = HttpClient("twitter", default_timeout=15)


class TwitterService:
    """Twitter/X API integration for cross-posting."""

    def __init__(self):
        self.api_key = getattr(settings, 'twitter_api_key', '')
        self.api_secret = getattr(settings, 'twitter_api_secret', '')
        self.access_token = getattr(settings, 'twitter_access_token', '')
        self.access_secret = getattr(settings, 'twitter_access_secret', '')
        self.base_url = "https://api.twitter.com/2"

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.api_secret and self.access_token and self.access_secret)

    def _auth(self) -> OAuth1:
        return OAuth1(
            self.api_key, self.api_secret,
            self.access_token, self.access_secret,
        )

    def post_tweet(self, text: str) -> Optional[str]:
        """Post a text-only tweet. Returns tweet ID or None."""
        if not self.is_configured:
            return None

        try:
            response = _http.post(
                f"{self.base_url}/tweets",
                json={"text": text[:280]},
                auth=self._auth(),
                timeout=15,
            )
            if response.status_code == 201:
                tweet_id = response.json().get('data', {}).get('id')
                logger.info(f"✅ Tweet posted: {tweet_id}")
                return tweet_id
            else:
                logger.error(f"Twitter post failed: {response.status_code} {response.text[:200]}")
                return None
        except Exception as e:
            logger.error(f"Twitter error: {e}")
            return None

    def post_tweet_with_media(self, text: str, image_bytes: bytes) -> Optional[str]:
        """Post a tweet with an image."""
        if not self.is_configured:
            return None

        try:
            # Step 1: Upload media
            media_response = _http.post(
                "https://upload.twitter.com/1.1/media/upload.json",
                files={"media": image_bytes},
                auth=self._auth(),
                timeout=30,
            )
            if media_response.status_code != 200:
                logger.error(f"Twitter media upload failed: {media_response.text[:200]}")
                return None

            media_id = media_response.json().get('media_id_string')

            # Step 2: Post tweet with media
            response = _http.post(
                f"{self.base_url}/tweets",
                json={
                    "text": text[:280],
                    "media": {"media_ids": [media_id]},
                },
                auth=self._auth(),
                timeout=15,
            )
            if response.status_code == 201:
                tweet_id = response.json().get('data', {}).get('id')
                logger.info(f"✅ Tweet with media posted: {tweet_id}")
                return tweet_id
            else:
                logger.error(f"Twitter post failed: {response.status_code}")
                return None
        except Exception as e:
            logger.error(f"Twitter error: {e}")
            return None

    def cross_post(self, caption: str, image_bytes: Optional[bytes] = None) -> bool:
        """Cross-post content from Facebook to Twitter."""
        # Truncate to Twitter's limit
        tweet_text = caption[:280]

        # Remove hashtags to save space (Twitter has its own)
        if len(tweet_text) > 250:
            tweet_text = tweet_text.rsplit('\n', 1)[0]

        if image_bytes:
            return self.post_tweet_with_media(tweet_text, image_bytes) is not None
        return self.post_tweet(tweet_text) is not None


twitter = TwitterService()
