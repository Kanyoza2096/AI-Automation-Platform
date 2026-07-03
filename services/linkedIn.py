"""
LinkedIn Integration Service v1.0
- Cross-post content to LinkedIn
- OAuth 2.0 authentication
- Professional audience targeting
"""

import logging
from typing import Optional

from config.settings import settings
from utils.http_client import HttpClient

logger = logging.getLogger("kanyoza.services.linkedin")

_http = HttpClient("linkedin", base_url="https://api.linkedin.com/v2", default_timeout=15)


class LinkedInService:
    """LinkedIn API integration for cross-posting."""

    def __init__(self):
        self.access_token = getattr(settings, 'linkedin_access_token', '')
        self.person_urn = getattr(settings, 'linkedin_person_urn', '')  # e.g., urn:li:person:12345
        self.base_url = "https://api.linkedin.com/v2"

    @property
    def is_configured(self) -> bool:
        return bool(self.access_token and self.person_urn)

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "X-Restli-Protocol-Version": "2.0.0",
        }

    def post_article(self, text: str, title: str = None, image_url: str = None) -> Optional[str]:
        """Post to LinkedIn feed. Returns post ID or None."""
        if not self.is_configured:
            return None

        try:
            payload = {
                "author": self.person_urn,
                "lifecycleState": "PUBLISHED",
                "specificContent": {
                    "com.linkedin.ugc.ShareContent": {
                        "shareCommentary": {
                            "text": text[:3000],
                        },
                        "shareMediaCategory": "NONE",
                    }
                },
                "visibility": {
                    "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC",
                },
            }

            response = _http.post(
                f"{self.base_url}/ugcPosts",
                headers=self._headers(),
                json=payload,
                timeout=15,
            )

            if response.status_code == 201:
                post_id = response.headers.get('X-RestLi-Id') or response.json().get('id')
                logger.info(f"✅ LinkedIn post published: {post_id}")
                return post_id
            else:
                logger.error(f"LinkedIn post failed: {response.status_code} {response.text[:200]}")
                return None
        except Exception as e:
            logger.error(f"LinkedIn error: {e}")
            return None

    def cross_post(self, caption: str) -> bool:
        """Cross-post content from Facebook to LinkedIn."""
        # LinkedIn prefers professional tone, strip emojis and hashtags
        professional_text = caption
        # Keep it clean for LinkedIn
        if len(professional_text) > 3000:
            professional_text = professional_text[:2997] + "..."

        return self.post_article(professional_text) is not None


linkedin = LinkedInService()
