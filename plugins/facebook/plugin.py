"""
Facebook Platform Plugin — Architecture §19-21.

Wraps FacebookService behind the Plugin interface.
Handles credential resolution, publishing, and error mapping.

Architecture §11 — PublishingResult as Canonical Contract:
  Every publish operation returns a structured PublishingResult.
  Success and failure are equally well-typed.

Architecture §13-14 — Retry Policy & Duplicate Side Effect Protection:
  Uses centralized RetryPolicy for API calls with idempotency_check.
  Before retrying a failed publish, checks if Facebook already has the post.
  Distinguishes "request failed before side effect" from
  "side effect may have succeeded" for idempotent retries.

Architecture §31 — Tenant Isolation:
  Credentials resolved by brand_id + platform, never global env vars
  (except in development mode with explicit config).
"""

from __future__ import annotations

import logging
from typing import Any, Optional, TYPE_CHECKING

from plugins.base import Plugin
from models.publishing_result import PublishingResult, PublishErrorType
from models.media_artifact import MediaArtifact
from services.retry_policy import RetryPolicy, OperationType, RetryConfig
from core.errors import (
    TokenMissingError,
    PlatformRateLimitedError,
    FacebookAPIError,
    KanyozaError,
    is_retryable,
)

if TYPE_CHECKING:
    from services.facebook import FacebookService
    from repositories.social_account_repository import SocialAccountRepository

logger = logging.getLogger(__name__)


class FacebookPlugin(Plugin):
    """Wraps FacebookService behind the Plugin interface.
    
    Architecture §20 — Platform Connectors:
      Plugin → Service → Connector → Graph API
      
    The plugin owns credential resolution and error mapping.
    The service owns API mechanics (HTTP, endpoints).
    The connector owns low-level communication.
    """

    def __init__(
        self,
        facebook_service: "FacebookService",
        social_repo: "SocialAccountRepository" = None,
    ) -> None:
        self._svc = facebook_service
        self._social_repo = social_repo
        self._active = False
        self._retry_policy = RetryPolicy.for_publish()

    @property
    def name(self) -> str:
        return "facebook"

    def start(self) -> None:
        self._active = True
        logger.info("FacebookPlugin: started")

    def stop(self) -> None:
        self._active = False
        logger.info("FacebookPlugin: stopped")

    # ═══════════════════════════════════════════════════════════════════
    # Credential Resolution
    # ═══════════════════════════════════════════════════════════════════

    def _resolve_credentials(
        self,
        brand: Optional[Any] = None,
    ) -> tuple[Optional[str], Optional[str]]:
        """Resolve Facebook credentials for a brand.
        
        Architecture §11 — Multi-Tenant Account Resolution:
          brand_id + "facebook" → SocialAccountRepository → page_id + token.
          Never uses accounts[0] or global env vars (except dev mode).
        
        Returns:
            (page_id, page_token) tuple. Both None if unresolvable.
        """
        page_id = None
        page_token = None

        # ── Primary: resolve from database via repository ────────────
        if brand and self._social_repo:
            try:
                logger.info(
                    "FacebookPlugin: looking up credentials for brand_id='%s', brand.name='%s'",
                    brand.id, brand.name,
                )

                account = self._social_repo.find_by_brand_and_platform(
                    brand.id, "facebook"
                )

                if account:
                    logger.info(
                        "FacebookPlugin: found account id=%s, platform_account_id=%s",
                        account.id, account.platform_account_id,
                    )
                    page_id = account.platform_account_id
                    page_token = getattr(account, 'access_token', None)

                    if not page_token:
                        pc = getattr(account, 'platform_config', {}) or {}
                        page_token = pc.get("page_access_token", "")

                    if page_id and page_token:
                        logger.info(
                            "FacebookPlugin: using brand-specific credentials (page=%s)",
                            page_id,
                        )
                    else:
                        logger.warning(
                            "FacebookPlugin: account found but missing credentials "
                            "(page_id=%s, has_token=%s)",
                            page_id, bool(page_token),
                        )
                else:
                    logger.warning(
                        "FacebookPlugin: no facebook account for brand '%s'",
                        brand.name,
                    )

            except Exception as exc:
                logger.error(
                    "FacebookPlugin: credential lookup failed for brand '%s': %s",
                    brand.name if brand else 'unknown', exc,
                )
                if not self._is_development():
                    raise TokenMissingError(
                        message=f"Failed to resolve Facebook credentials for brand '{brand.name if brand else 'unknown'}'",
                        cause=exc,
                        context={"brand_id": brand.id if brand else None},
                    )

        # ── Fallback: global env vars (development only) ──────────────
        if not page_token and self._is_development():
            from config.settings import settings
            if settings.page_access_token:
                page_id = settings.page_id
                page_token = settings.page_access_token
                logger.warning(
                    "FacebookPlugin: DEV MODE — using global env vars (page=%s)",
                    page_id,
                )

        return page_id, page_token

    def _is_development(self) -> bool:
        """Check if running in development mode."""
        try:
            from config.settings import settings
            return getattr(settings, 'is_development', False)
        except Exception:
            return False

    # ═══════════════════════════════════════════════════════════════════
    # Publishing
    # ═══════════════════════════════════════════════════════════════════

    def publish(
        self,
        content: str,
        image_url: Optional[str] = None,
        image_bytes: Optional[bytes] = None,
        brand: Optional[Any] = None,
        media_artifact: Optional[MediaArtifact] = None,
        **kwargs,
    ) -> dict[str, Any]:
        """Publish content to a Facebook Page.
        
        Args:
            content: Text content for the post
            image_url: Public URL of image (mutually exclusive with image_bytes)
            image_bytes: Raw image bytes for direct upload
            brand: Brand model for credential resolution
            media_artifact: MediaArtifact with status (preferred over raw bytes)
        
        Returns:
            PublishingResult as dict with ok, post_id, error details.
        """
        brand_id = getattr(brand, 'id', None) if brand else None

        # ── Pre-flight checks ────────────────────────────────────────
        if not self._active:
            return PublishingResult.failure(
                "facebook",
                PublishErrorType.PLUGIN_NOT_STARTED,
                "Plugin not started",
                brand_id=brand_id,
            ).to_dict()

        if not content or not content.strip():
            return PublishingResult.failure(
                "facebook",
                PublishErrorType.CONTENT_EMPTY,
                "Content is empty",
                brand_id=brand_id,
            ).to_dict()

        # ── Resolve media from MediaArtifact if provided ──────────────
        if media_artifact is not None and image_bytes is None:
            if media_artifact.is_valid:
                image_bytes = media_artifact.bytes
                logger.info(
                    "FacebookPlugin: using media from MediaArtifact (%s, %dKB)",
                    media_artifact.source, media_artifact.size_kb,
                )
            elif media_artifact.has_failed:
                logger.warning(
                    "FacebookPlugin: MediaArtifact indicates render failed: %s. "
                    "Proceeding text-only.",
                    media_artifact.error,
                )
            elif media_artifact.is_text_only:
                logger.info("FacebookPlugin: MediaArtifact indicates text-only post.")
            elif media_artifact.is_pending:
                logger.warning(
                    "FacebookPlugin: MediaArtifact indicates render in progress. "
                    "Proceeding text-only; image may be added later."
                )

        # ── Resolve credentials ──────────────────────────────────────
        page_id, page_token = self._resolve_credentials(brand)

        if not page_token:
            return PublishingResult.failure(
                "facebook",
                PublishErrorType.TOKEN_MISSING,
                f"No Facebook credentials for brand '{getattr(brand, 'name', 'unknown')}'",
                brand_id=brand_id,
            ).to_dict()

        # ── Publish with retry + idempotency check ───────────────────
        logger.info(
            "FacebookPlugin.publish: content='%s' (len=%d), page_id=%s, has_image=%s",
            content[:100], len(content), page_id, bool(image_bytes),
        )

        try:
            result = self._retry_policy.execute(
                operation=lambda: self._svc.post_to_page(
                    message=content,
                    image_bytes=image_bytes or None,
                    image_url=image_url if not image_bytes else None,
                    page_id=page_id,
                    page_token=page_token,
                ),
                idempotency_check=lambda: self._svc.check_recent_duplicate(
                    message=content,
                    page_id=page_id,
                    page_token=page_token,
                ),
                operation_name="facebook_publish",
            )

            if result.success and result.value:
                value = result.value
                if value.get("ok"):
                    post_id = value.get("post_id")
                    logger.info(
                        "FacebookPlugin.publish: success — post_id=%s (attempts=%d)",
                        post_id, result.attempts,
                    )
                    return PublishingResult.success(
                        "facebook",
                        post_id,
                        brand_id=brand_id,
                        platform_account_id=page_id,
                        duration_ms=value.get("duration_ms"),
                    ).to_dict()
                else:
                    return self._map_service_error(value, brand_id, page_id)
            else:
                error = result.error or RuntimeError("Unknown publish error")
                return self._map_exception(error, brand_id, page_id)

        except Exception as exc:
            logger.error("FacebookPlugin.publish: exception — %s", exc, exc_info=True)
            return PublishingResult.failure(
                "facebook",
                PublishErrorType.UNKNOWN_ERROR,
                str(exc),
                brand_id=brand_id,
            ).to_dict()

    # ═══════════════════════════════════════════════════════════════════
    # Error Mapping
    # ═══════════════════════════════════════════════════════════════════

    def _map_service_error(
        self,
        result: dict,
        brand_id: Optional[str],
        page_id: Optional[str],
    ) -> dict:
        """Map a structured service error to PublishingResult."""
        error_info = result.get("error", {})
        stage = error_info.get("stage", "unknown")
        message = error_info.get("message", "Unknown error")
        retryable = error_info.get("retryable", True)

        error_type = self._classify_error(stage, message)

        logger.error(
            "FacebookPlugin.publish: failed — type=%s, stage=%s, message=%s",
            error_type.value, stage, message,
        )

        return PublishingResult.failure(
            "facebook",
            error_type,
            message,
            brand_id=brand_id,
            platform_account_id=page_id,
            retryable=retryable,
        ).to_dict()

    def _map_exception(
        self,
        exc: Exception,
        brand_id: Optional[str],
        page_id: Optional[str],
    ) -> dict:
        """Map an exception to PublishingResult."""
        if isinstance(exc, KanyozaError):
            error_type = self._error_code_to_publish_error(exc.code)
            return PublishingResult.failure(
                "facebook",
                error_type,
                exc.message,
                brand_id=brand_id,
                platform_account_id=page_id,
                retryable=exc.retryable,
            ).to_dict()

        retryable = is_retryable(exc)
        return PublishingResult.failure(
            "facebook",
            PublishErrorType.UNKNOWN_ERROR,
            str(exc),
            brand_id=brand_id,
            platform_account_id=page_id,
            retryable=retryable,
        ).to_dict()

    @staticmethod
    def _classify_error(stage: str, message: str) -> PublishErrorType:
        """Classify an error based on stage and message content."""
        message_lower = message.lower()

        if stage == "credential_check":
            return PublishErrorType.TOKEN_MISSING
        elif stage == "facebook_auth":
            return PublishErrorType.INVALID_CREDENTIALS
        elif "permission" in message_lower or "access denied" in message_lower:
            return PublishErrorType.FACEBOOK_PERMISSION_ERROR
        elif "rate" in message_lower or "throttl" in message_lower:
            return PublishErrorType.FACEBOOK_RATE_LIMITED
        elif stage in ("facebook_api", "facebook_service"):
            return PublishErrorType.FACEBOOK_API_ERROR
        elif "timeout" in message_lower or "connection" in message_lower:
            return PublishErrorType.FACEBOOK_API_ERROR

        return PublishErrorType.UNKNOWN_ERROR

    @staticmethod
    def _error_code_to_publish_error(code) -> PublishErrorType:
        """Map core ErrorCode to PublishErrorType."""
        from core.errors import ErrorCode

        mapping = {
            ErrorCode.TOKEN_MISSING: PublishErrorType.TOKEN_MISSING,
            ErrorCode.TOKEN_EXPIRED: PublishErrorType.INVALID_CREDENTIALS,
            ErrorCode.TOKEN_INVALID: PublishErrorType.INVALID_CREDENTIALS,
            ErrorCode.INVALID_CREDENTIALS: PublishErrorType.INVALID_CREDENTIALS,
            ErrorCode.PERMISSION_DENIED: PublishErrorType.FACEBOOK_PERMISSION_ERROR,
            ErrorCode.PAGE_ACCESS_DENIED: PublishErrorType.FACEBOOK_PERMISSION_ERROR,
            ErrorCode.PLATFORM_RATE_LIMITED: PublishErrorType.FACEBOOK_RATE_LIMITED,
            ErrorCode.FACEBOOK_API_ERROR: PublishErrorType.FACEBOOK_API_ERROR,
            ErrorCode.FACEBOOK_PERMISSION_ERROR: PublishErrorType.FACEBOOK_PERMISSION_ERROR,
            ErrorCode.NETWORK_TIMEOUT: PublishErrorType.FACEBOOK_API_ERROR,
            ErrorCode.NETWORK_ERROR: PublishErrorType.FACEBOOK_API_ERROR,
        }
        return mapping.get(code, PublishErrorType.UNKNOWN_ERROR)

    # ═══════════════════════════════════════════════════════════════════
    # Health Check
    # ═══════════════════════════════════════════════════════════════════

    def health(self, account=None) -> dict[str, Any]:
        """Check if the plugin can authenticate with Facebook.
        
        When called with an account (from API health-check endpoint),
        makes a direct Facebook API call with that account's token.
        When called without an account, checks global config.
        """
        try:
            if account:
                page_id = getattr(account, 'platform_account_id', None)
                page_token = getattr(account, 'access_token', None)
                if not page_token:
                    pc = getattr(account, 'platform_config', {}) or {}
                    page_token = pc.get('page_access_token', '')
                
                if page_id and page_token:
                    import requests as _requests
                    resp = _requests.get(
                        f"https://graph.facebook.com/v22.0/{page_id}",
                        params={"fields": "name", "access_token": page_token},
                        timeout=8,
                    )
                    ok = resp.status_code == 200
                else:
                    ok = False
            else:
                page_id, page_token = self._resolve_credentials()
                ok = bool(page_token)
        except Exception:
            ok = False
        
        return {
            "ok": ok,
            "platform": "facebook",
            "active": self._active,
        }

    # ═══════════════════════════════════════════════════════════════════
    # Connector
    # ═══════════════════════════════════════════════════════════════════

    @property
    def connector(self) -> Optional[Any]:
        from services.connectors.facebook_connector import FacebookConnector
        return FacebookConnector(self._svc)
