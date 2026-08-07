"""
Facebook Graph API client v8.5
Handles Messenger messages, page posts, comment replies, typing indicators,
page growth (invites, cross-engagement), and health checks.

v8.5 changes:
- post_to_page() and internal helpers now RAISE on retryable failures
  so RetryPolicy in FacebookPlugin can actually retry them.
- Non-retryable errors (auth, permissions) still return structured dicts.
- check_recent_duplicate() now uses 'since' timestamp to only check
  recent posts within the specified minutes window.
"""

import io
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List

from config.settings import settings
from utils.http_client import HttpClient
from utils.retry import smart_retry

logger = logging.getLogger("kanyoza.services.facebook")

GRAPH_BASE = "https://graph.facebook.com/v22.0"
_REQUEST_TIMEOUT = 15

RETRYABLE_ERROR_CODES = {1, 2, 4, 17, 341, 368, 613, 80000, 80001, 80002, 80003}
NON_RETRYABLE_ERROR_CODES = {190, 102, 463, 464, 467}  # Auth, permission errors


def _emit_dashboard_event(event_name: str, payload: dict) -> None:
    """Fire-and-forget SocketIO emission for the Enterprise Dashboard."""
    try:
        import asyncio
        from core.socket_server import emit_to_dashboard

        loop = asyncio.get_running_loop()
        asyncio.run_coroutine_threadsafe(
            emit_to_dashboard(event_name, payload),
            loop,
        )
    except RuntimeError:
        pass
    except Exception as exc:
        logger.debug("emit_dashboard_event error (%s): %s", event_name, exc)


class FacebookServiceError(Exception):
    def __init__(self, code: int, message: str, retryable: bool = True):
        self.code = code
        self.retryable = retryable
        super().__init__(f"API error ({code}): {message}")


class FacebookAuthError(Exception):
    def __init__(self, code: int, message: str):
        self.code = code
        super().__init__(f"Auth error ({code}): {message}")


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
        if error_code in NON_RETRYABLE_ERROR_CODES:
            raise FacebookAuthError(error_code, error_message)
        if error_code in RETRYABLE_ERROR_CODES:
            raise FacebookServiceError(error_code, error_message, retryable=True)
        raise FacebookServiceError(error_code, error_message, retryable=True)

    # ==================================================================
    # MESSENGER
    # ==================================================================

    @smart_retry(max_retries=3, base_delay=1.0)
    def send_message(self, recipient_psid: str, message: str) -> bool:
        if not self.is_configured:
            return False
        url = f"{GRAPH_BASE}/me/messages"
        payload = {
            "recipient": {"id": recipient_psid},
            "message": {"text": message[:2000]},
            "access_token": self.page_token,
        }
        try:
            response = self._session.post(url, json=payload, timeout=_REQUEST_TIMEOUT)
            result = self._handle_response(response, "send_message")
            success = "message_id" in result
            if success:
                _emit_dashboard_event("new_message", {
                    "id": result.get("message_id", ""),
                    "user": recipient_psid,
                    "message": message[:100],
                    "time": __import__('time').time() * 1000,
                    "sentiment": "neutral",
                })
            return success
        except FacebookAuthError:
            logger.critical("Facebook auth error")
            return False
        except Exception as e:
            logger.error("send_message failed: %s", e)
            return False

    def send_typing_on(self, recipient_psid: str) -> None:
        if not self.is_configured:
            return
        try:
            self._session.post(
                f"{GRAPH_BASE}/me/messages",
                json={
                    "recipient": {"id": recipient_psid},
                    "sender_action": "typing_on",
                    "access_token": self.page_token,
                },
                timeout=5,
            )
        except Exception:
            pass

    def send_typing_off(self, recipient_psid: str) -> None:
        if not self.is_configured:
            return
        try:
            self._session.post(
                f"{GRAPH_BASE}/me/messages",
                json={
                    "recipient": {"id": recipient_psid},
                    "sender_action": "typing_off",
                    "access_token": self.page_token,
                },
                timeout=5,
            )
        except Exception:
            pass

    def send_mark_seen(self, recipient_psid: str) -> None:
        if not self.is_configured:
            return
        try:
            self._session.post(
                f"{GRAPH_BASE}/me/messages",
                json={
                    "recipient": {"id": recipient_psid},
                    "sender_action": "mark_seen",
                    "access_token": self.page_token,
                },
                timeout=5,
            )
        except Exception:
            pass

    # ==================================================================
    # PAGE POSTS — Raises on retryable failures for RetryPolicy
    # ==================================================================

    @smart_retry(max_retries=3, base_delay=2.0)
    def post_to_page(
        self,
        message: str,
        image_url: Optional[str] = None,
        image_bytes: Optional[bytes] = None,
        page_id: Optional[str] = None,
        page_token: Optional[str] = None,
    ) -> dict:
        """
        Publish a post to a Facebook page.
        
        Returns a structured dict on success or non-retryable failure.
        RAISES on retryable failures so RetryPolicy can catch and retry.
        
        Returns:
            {"ok": True, "post_id": "123", "platform": "facebook"}
            {"ok": False, "error": {...}} — only for non-retryable errors
        """
        pid = page_id or self.page_id
        ptoken = page_token or self.page_token

        if not pid or not ptoken:
            return {
                "ok": False,
                "error": {
                    "stage": "credential_check",
                    "message": "Missing page_id or page_token",
                    "retryable": False
                }
            }

        logger.info(
            "Facebook post_to_page: message='%s' (len=%d), has_image_bytes=%s, has_image_url=%s, page_id=%s",
            message[:100] if message else "(empty)",
            len(message) if message else 0,
            bool(image_bytes),
            bool(image_url),
            pid,
        )

        try:
            if image_bytes:
                post_id = self._post_photo_binary(message, image_bytes, pid, ptoken)
            elif image_url:
                post_id = self._post_photo_url(message, image_url, pid, ptoken)
            else:
                post_id = self._post_text(message, pid, ptoken)

            _emit_dashboard_event("post_published", {
                "id": post_id,
                "title": (message or "New Post")[:100],
                "platform": "facebook",
                "time": __import__('time').time() * 1000,
                "engagement": 0,
                "thumbnail": image_url or "",
            })
            return {"ok": True, "post_id": post_id, "platform": "facebook"}

        except FacebookAuthError as e:
            return {
                "ok": False,
                "error": {
                    "stage": "facebook_auth",
                    "code": e.code,
                    "message": str(e),
                    "retryable": False
                }
            }
        except FacebookServiceError as e:
            if e.retryable:
                raise  # Let RetryPolicy handle retryable errors
            return {
                "ok": False,
                "error": {
                    "stage": "facebook_service",
                    "code": e.code,
                    "message": str(e),
                    "retryable": False
                }
            }
        except Exception as e:
            raise FacebookServiceError(code=-1, message=str(e), retryable=True)

    def _post_text(self, message: str, page_id: str, page_token: str) -> str:
        """Post text to Facebook. Raises on failure, returns post_id on success."""
        if not message or not message.strip():
            raise FacebookServiceError(code=-1, message="Message is empty", retryable=False)

        try:
            response = self._session.post(
                f"{GRAPH_BASE}/{page_id}/feed",
                data={"message": message, "access_token": page_token},
                timeout=30,
            )
            result = self._handle_response(response, "post_text")
            post_id = result.get("id")
            if post_id:
                logger.info("Facebook _post_text: success — post_id=%s", post_id)
                return post_id
            logger.warning("Facebook _post_text: no id in response — %s", result)
            raise FacebookServiceError(code=-1, message="No post ID returned", retryable=True)
        except (FacebookAuthError, FacebookServiceError):
            raise
        except Exception as e:
            logger.error("Facebook _post_text unexpected error: %s", e)
            raise FacebookServiceError(code=-1, message=str(e), retryable=True)

    def _post_photo_url(self, message: str, image_url: str, page_id: str, page_token: str) -> str:
        """Post photo URL to Facebook. Raises on failure, returns post_id on success."""
        try:
            response = self._session.post(
                f"{GRAPH_BASE}/{page_id}/photos",
                data={
                    "url": image_url,
                    "caption": message[:2000] if message else "",
                    "access_token": page_token,
                    "published": "true",
                },
                timeout=30,
            )
            result = self._handle_response(response, "post_photo_url")
            post_id = result.get("id") or result.get("post_id")
            if post_id:
                logger.info("Facebook _post_photo_url: success — post_id=%s", post_id)
                return post_id
            logger.warning("Facebook _post_photo_url: no id in response — %s", result)
            raise FacebookServiceError(code=-1, message="No post ID returned", retryable=True)
        except (FacebookAuthError, FacebookServiceError):
            raise
        except Exception as e:
            logger.error("Facebook _post_photo_url unexpected error: %s", e)
            raise FacebookServiceError(code=-1, message=str(e), retryable=True)

    def _post_photo_binary(self, message: str, image_bytes: bytes, page_id: str, page_token: str) -> str:
        """Post photo to Facebook. Raises on failure, returns post_id on success."""
        try:
            payload = {
                "caption": message[:2000] if message else "",
                "access_token": page_token,
                "published": "true",
            }
            files = {"source": ("thumbnail.png", io.BytesIO(image_bytes), "image/png")}
            response = self._session.post(
                f"{GRAPH_BASE}/{page_id}/photos",
                data=payload,
                files=files,
                timeout=45,
            )
            result = self._handle_response(response, "post_photo_binary")
            post_id = result.get("id") or result.get("post_id")
            if post_id:
                logger.info("Facebook _post_photo_binary: success — post_id=%s", post_id)
                return post_id
            logger.warning("Facebook _post_photo_binary: no id in response — %s", result)
            raise FacebookServiceError(code=-1, message="No post ID returned", retryable=True)
        except FacebookAuthError:
            raise
        except FacebookServiceError:
            raise
        except Exception as e:
            logger.error("Facebook _post_photo_binary unexpected error: %s", e)
            raise FacebookServiceError(code=-1, message=str(e), retryable=True)

    # ==================================================================
    # IDEMPOTENCY CHECK — Architecture §14
    # ==================================================================

    def check_recent_duplicate(
        self,
        message: str,
        page_id: Optional[str] = None,
        page_token: Optional[str] = None,
        minutes: int = 10,
    ) -> bool:
        """
        Check if a post with the same content was recently published.
        
        Only checks posts from the last N minutes using the 'since' parameter.
        
        Returns:
            True if a duplicate was found (don't retry)
            False if no duplicate (safe to retry)
        """
        pid = page_id or self.page_id
        ptoken = page_token or self.page_token

        if not pid or not ptoken:
            return False

        try:
            since_timestamp = int((datetime.now(timezone.utc) - timedelta(minutes=minutes)).timestamp())

            response = self._session.get(
                f"{GRAPH_BASE}/{pid}/feed",
                params={
                    "access_token": ptoken,
                    "limit": 5,
                    "fields": "message,created_time",
                    "since": since_timestamp,
                },
                timeout=10,
            )

            if response.status_code != 200:
                logger.debug("check_recent_duplicate: API returned %s", response.status_code)
                return False

            data = response.json().get("data", [])
            if not data:
                return False

            normalized_new = message.strip()[:100].lower()

            for post in data:
                post_message = (post.get("message") or "").strip()[:100].lower()
                if post_message == normalized_new:
                    logger.warning(
                        "check_recent_duplicate: FOUND duplicate post (id=%s, created=%s) — "
                        "previous publish likely succeeded despite error",
                        post.get("id"), post.get("created_time"),
                    )
                    return True

            return False

        except Exception as e:
            logger.debug("check_recent_duplicate error: %s", e)
            return False

    # ==================================================================
    # COMMENT ENGAGEMENT
    # ==================================================================

    def get_recent_posts(self, limit: int = 5) -> List[Dict]:
        if not self.is_configured:
            return []
        try:
            response = self._session.get(
                f"{GRAPH_BASE}/{self.page_id}/posts",
                params={
                    "access_token": self.page_token,
                    "limit": limit,
                    "fields": "id,message,created_time",
                },
                timeout=_REQUEST_TIMEOUT,
            )
            return self._handle_response(response, "get_recent_posts").get("data", [])
        except Exception as e:
            logger.error("Facebook get_recent_posts failed: %s", e)
            return []

    def get_post_comments(self, post_id: str, limit: int = 20, after: Optional[str] = None) -> tuple:
        params = {
            "access_token": self.page_token,
            "limit": limit,
            "order": "reverse_chronological",
            "fields": "id,message,from,created_time",
            "filter": "stream",
        }
        if after:
            params["after"] = after
        try:
            response = self._session.get(
                f"{GRAPH_BASE}/{post_id}/comments",
                params=params,
                timeout=_REQUEST_TIMEOUT,
            )
            result = self._handle_response(response, "get_post_comments")
            return result.get("data", []), result.get("paging", {}).get("cursors", {}).get("after")
        except Exception as e:
            logger.error("Facebook get_post_comments failed (post_id=%s): %s", post_id, e)
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
            response = self._session.post(
                f"{GRAPH_BASE}/{comment_id}/comments",
                data={"message": message, "access_token": self.page_token},
                timeout=_REQUEST_TIMEOUT,
            )
            self._handle_response(response, "reply_to_comment")
            return True
        except Exception as e:
            logger.error("Facebook reply_to_comment failed (comment_id=%s): %s", comment_id, e)
            return False

    def like_comment(self, comment_id: str) -> bool:
        if not self.is_configured:
            return False
        try:
            resp = self._session.post(
                f"{GRAPH_BASE}/{comment_id}/likes",
                data={"access_token": self.page_token},
                timeout=10,
            )
            if resp.status_code == 200:
                return resp.json().get("success", True)
            logger.error("Facebook like_comment failed (comment_id=%s): HTTP %s — %s", comment_id, resp.status_code, resp.text[:200])
            return False
        except Exception as e:
            logger.error("Facebook like_comment error (comment_id=%s): %s", comment_id, e)
            return False

    # ==================================================================
    # PAGE GROWTH
    # ==================================================================

    def invite_to_follow(self, user_id: str) -> bool:
        if not self.is_configured:
            return False
        try:
            resp = self._session.post(
                f"{GRAPH_BASE}/{self.page_id}/invites",
                data={"recipient": user_id, "access_token": self.page_token},
                timeout=10,
            )
            if resp.status_code in (200, 201):
                return resp.json().get("success", True)
            logger.error("Facebook invite_to_follow failed (user_id=%s): HTTP %s — %s", user_id, resp.status_code, resp.text[:200])
            return False
        except Exception as e:
            logger.error("Facebook invite_to_follow error (user_id=%s): %s", user_id, e)
            return False

    def get_page_posts(self, page_id: str, limit: int = 3) -> List[Dict]:
        try:
            response = self._session.get(
                f"{GRAPH_BASE}/{page_id}/posts",
                params={
                    "access_token": self.page_token,
                    "limit": limit,
                    "fields": "id,message,created_time",
                },
                timeout=_REQUEST_TIMEOUT,
            )
            return response.json().get("data", [])
        except Exception:
            return []

    def comment_on_post(self, post_id: str, message: str) -> bool:
        if not self.is_configured:
            return False
        try:
            resp = self._session.post(
                f"{GRAPH_BASE}/{post_id}/comments",
                data={"message": message, "access_token": self.page_token},
                timeout=_REQUEST_TIMEOUT,
            )
            if resp.status_code in (200, 201):
                return bool(resp.json().get("id") or resp.json().get("success"))
            logger.error("Facebook comment_on_post failed (post_id=%s): HTTP %s — %s", post_id, resp.status_code, resp.text[:200])
            return False
        except Exception as e:
            logger.error("Facebook comment_on_post error (post_id=%s): %s", post_id, e)
            return False

    def like_post(self, post_id: str) -> bool:
        try:
            return (
                self._session.post(
                    f"{GRAPH_BASE}/{post_id}/likes",
                    data={"access_token": self.page_token},
                    timeout=10,
                ).status_code
                == 200
            )
        except Exception:
            return False

    # ==================================================================
    # HEALTH — Improved Validation
    # ==================================================================

    def validate_token(self) -> dict:
        if not self.page_token:
            return {"ok": False, "error": "No page token configured"}

        try:
            response = self._session.get(
                f"{GRAPH_BASE}/me",
                params={"fields": "id,name", "access_token": self.page_token},
                timeout=_REQUEST_TIMEOUT,
            )
            if response.status_code == 200:
                data = response.json()
                return {
                    "ok": True,
                    "page_id": data.get("id"),
                    "page_name": data.get("name"),
                }
            else:
                error = response.json().get("error", {})
                return {
                    "ok": False,
                    "error": {
                        "code": error.get("code"),
                        "message": error.get("message", "Unknown error"),
                    }
                }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def validate_publish_capability(self, page_id: str = None) -> dict:
        pid = page_id or self.page_id
        if not pid or not self.page_token:
            return {"ok": False, "error": "Missing page_id or page_token"}

        try:
            response = self._session.get(
                f"{GRAPH_BASE}/{pid}",
                params={
                    "fields": "name,access_token",
                    "access_token": self.page_token,
                },
                timeout=_REQUEST_TIMEOUT,
            )
            if response.status_code == 200:
                data = response.json()
                return {
                    "ok": True,
                    "page_id": pid,
                    "page_name": data.get("name"),
                    "can_publish": True,
                }
            else:
                error = response.json().get("error", {})
                return {
                    "ok": False,
                    "can_publish": False,
                    "error": {
                        "code": error.get("code"),
                        "message": error.get("message"),
                    }
                }
        except Exception as e:
            return {"ok": False, "can_publish": False, "error": str(e)}

    def close(self):
        self._session.close()


facebook = FacebookService()
