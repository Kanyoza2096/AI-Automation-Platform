"""
Facebook Graph API client v8.0
Handles Messenger messages, page posts, comment replies, typing indicators,
page growth (invites, cross-engagement), and health checks.
"""

import io
import logging
from typing import Optional, Dict, Any, List

from config.settings import settings
from utils.http_client import HttpClient
from utils.retry import smart_retry

logger = logging.getLogger("kanyoza.services.facebook")

GRAPH_BASE = "https://graph.facebook.com/v22.0"
_REQUEST_TIMEOUT = 15

RETRYABLE_ERROR_CODES = {1, 2, 4, 17, 341, 368, 613, 80000, 80001, 80002, 80003}


class FacebookServiceError(Exception):
    pass


class FacebookAuthError(Exception):
    pass


class FacebookService:
    """Facebook Graph API integration with connection pooling."""

    def __init__(self):
        self.page_token = settings.page_access_token
        self.page_id = settings.page_id
        self._session = HttpClient(
            "facebook",
            base_url=GRAPH_BASE,
            default_timeout=_REQUEST_TIMEOUT,
            default_headers={"Accept": "application/json"},
        )

    @property
    def is_configured(self) -> bool:
        return bool(self.page_token and self.page_id)

    def reload_config(self):
        self.page_token = settings.page_access_token
        self.page_id = settings.page_id

    def _handle_response(self, response, context: str) -> dict:
        result = response.json() if response.text else {}
        if response.status_code == 200:
            return result
        error = result.get("error", {})
        error_code = error.get("code", -1)
        error_message = error.get("message", "Unknown error")
        if error_code in (190, 102, 463, 464, 467):
            raise FacebookAuthError(f"Auth error ({error_code}): {error_message}")
        if error_code in RETRYABLE_ERROR_CODES:
            raise ConnectionError(f"Retryable error ({error_code}): {error_message}")
        raise FacebookServiceError(f"API error ({error_code}): {error_message} [ctx={context}]")

    # ==================================================================
    # MESSENGER
    # ==================================================================

    @smart_retry(max_retries=3, base_delay=1.0)
    def send_message(self, recipient_psid: str, message: str) -> bool:
        if not self.is_configured:
            return False
        url = f"{GRAPH_BASE}/me/messages"
        payload = {"recipient": {"id": recipient_psid}, "message": {"text": message[:2000]}, "access_token": self.page_token}
        try:
            response = self._session.post(url, json=payload, timeout=_REQUEST_TIMEOUT)
            result = self._handle_response(response, "send_message")
            return "message_id" in result
        except FacebookAuthError:
            logger.critical("Facebook auth error")
            return False
        except Exception as e:
            logger.error(f"send_message failed: {e}")
            return False

    def send_typing_on(self, recipient_psid: str) -> None:
        if not self.is_configured:
            return
        try:
            self._session.post(f"{GRAPH_BASE}/me/messages", json={"recipient": {"id": recipient_psid}, "sender_action": "typing_on", "access_token": self.page_token}, timeout=5)
        except Exception:
            pass

    def send_typing_off(self, recipient_psid: str) -> None:
        if not self.is_configured:
            return
        try:
            self._session.post(f"{GRAPH_BASE}/me/messages", json={"recipient": {"id": recipient_psid}, "sender_action": "typing_off", "access_token": self.page_token}, timeout=5)
        except Exception:
            pass

    def send_mark_seen(self, recipient_psid: str) -> None:
        if not self.is_configured:
            return
        try:
            self._session.post(f"{GRAPH_BASE}/me/messages", json={"recipient": {"id": recipient_psid}, "sender_action": "mark_seen", "access_token": self.page_token}, timeout=5)
        except Exception:
            pass

    # ==================================================================
    # PAGE POSTS
    # ==================================================================

    @smart_retry(max_retries=3, base_delay=2.0)
    def post_to_page(self, message: str, image_url: Optional[str] = None, image_bytes: Optional[bytes] = None, page_id: Optional[str] = None, page_token: Optional[str] = None) -> bool:
        pid = page_id or self.page_id
        ptoken = page_token or self.page_token
        
        if not pid or not ptoken:
            return False
            
        if image_bytes:
            return self._post_photo_binary(message, image_bytes, pid, ptoken)
        if image_url:
            return self._post_photo_url(message, image_url, pid, ptoken)
        return self._post_text(message, pid, ptoken)

    def _post_text(self, message: str, page_id: str, page_token: str) -> bool:
        try:
            response = self._session.post(f"{GRAPH_BASE}/{page_id}/feed", data={"message": message, "access_token": page_token}, timeout=30)
            result = self._handle_response(response, "post_text")
            return "id" in result
        except Exception:
            return False

    def _post_photo_url(self, message: str, image_url: str, page_id: str, page_token: str) -> bool:
        try:
            response = self._session.post(f"{GRAPH_BASE}/{page_id}/photos", data={"url": image_url, "caption": message[:2000], "access_token": page_token, "published": "true"}, timeout=30)
            result = self._handle_response(response, "post_photo_url")
            return "id" in result or "post_id" in result
        except Exception:
            return self._post_text(message, page_id, page_token)

    def _post_photo_binary(self, message: str, image_bytes: bytes, page_id: str, page_token: str) -> bool:
        try:
            payload = {"caption": message[:2000], "access_token": page_token, "published": "true"}
            files = {"source": ("thumbnail.png", io.BytesIO(image_bytes), "image/png")}
            response = self._session.post(f"{GRAPH_BASE}/{page_id}/photos", data=payload, files=files, timeout=45)
            result = self._handle_response(response, "post_photo_binary")
            return "id" in result or "post_id" in result
        except Exception:
            return False

    # ==================================================================
    # COMMENT ENGAGEMENT
    # ==================================================================

    def get_recent_posts(self, limit: int = 5) -> List[Dict]:
        if not self.is_configured:
            return []
        try:
            response = self._session.get(f"{GRAPH_BASE}/{self.page_id}/posts", params={"access_token": self.page_token, "limit": limit, "fields": "id,message,created_time"}, timeout=_REQUEST_TIMEOUT)
            return self._handle_response(response, "get_recent_posts").get("data", [])
        except Exception:
            return []

    def get_post_comments(self, post_id: str, limit: int = 20, after: Optional[str] = None) -> tuple:
        params = {"access_token": self.page_token, "limit": limit, "order": "reverse_chronological", "fields": "id,message,from,created_time", "filter": "stream"}
        if after:
            params["after"] = after
        try:
            response = self._session.get(f"{GRAPH_BASE}/{post_id}/comments", params=params, timeout=_REQUEST_TIMEOUT)
            result = self._handle_response(response, "get_post_comments")
            return result.get("data", []), result.get("paging", {}).get("cursors", {}).get("after")
        except Exception:
            return [], None

    def get_unreplied_comments(self, post_id: str, max_fetch: int = 60) -> List[Dict]:
        all_comments, after, fetched = [], None, 0
        while fetched < max_fetch:
            batch, after = self.get_post_comments(post_id, limit=min(20, max_fetch - fetched), after=after)
            if not batch:
                break
            all_comments.extend(batch)
            fetched += len(batch)
            if not after:
                break
        return all_comments

    @smart_retry(max_retries=2, base_delay=1.0)
    def reply_to_comment(self, comment_id: str, message: str) -> bool:
        if not self.is_configured:
            return False
        try:
            response = self._session.post(f"{GRAPH_BASE}/{comment_id}/comments", data={"message": message, "access_token": self.page_token}, timeout=_REQUEST_TIMEOUT)
            self._handle_response(response, "reply_to_comment")
            return True
        except Exception:
            return False

    def like_comment(self, comment_id: str) -> bool:
        if not self.is_configured:
            return False
        try:
            return self._session.post(f"{GRAPH_BASE}/{comment_id}/likes", data={"access_token": self.page_token}, timeout=10).status_code == 200
        except Exception:
            return False

    # ==================================================================
    # PAGE GROWTH
    # ==================================================================

    def invite_to_follow(self, user_id: str) -> bool:
        if not self.is_configured:
            return False
        try:
            return self._session.post(f"{GRAPH_BASE}/{self.page_id}/invites", data={"recipient": user_id, "access_token": self.page_token}, timeout=10).status_code == 200
        except Exception:
            return False

    def get_page_posts(self, page_id: str, limit: int = 3) -> List[Dict]:
        try:
            response = self._session.get(f"{GRAPH_BASE}/{page_id}/posts", params={"access_token": self.page_token, "limit": limit, "fields": "id,message,created_time"}, timeout=_REQUEST_TIMEOUT)
            return response.json().get("data", [])
        except Exception:
            return []

    def comment_on_post(self, post_id: str, message: str) -> bool:
        if not self.is_configured:
            return False
        try:
            return self._session.post(f"{GRAPH_BASE}/{post_id}/comments", data={"message": message, "access_token": self.page_token}, timeout=_REQUEST_TIMEOUT).status_code == 200
        except Exception:
            return False

    def like_post(self, post_id: str) -> bool:
        try:
            return self._session.post(f"{GRAPH_BASE}/{post_id}/likes", data={"access_token": self.page_token}, timeout=10).status_code == 200
        except Exception:
            return False

    # ==================================================================
    # HEALTH
    # ==================================================================

    def validate_token(self) -> bool:
        try:
            return self._session.get(f"{GRAPH_BASE}/me", params={"fields": "id,name", "access_token": self.page_token}, timeout=_REQUEST_TIMEOUT).status_code == 200
        except Exception:
            return False

    def close(self):
        self._session.close()


facebook = FacebookService()
