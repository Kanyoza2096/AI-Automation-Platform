"""
Structured publishing result types for the Kanyoza platform.
Replaces generic None/Exception returns with typed errors.
"""

from __future__ import annotations
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional


class PublishErrorType(str, Enum):
    """Typed publishing errors — never return None again."""
    NO_BRAND_CONTEXT = "NO_BRAND_CONTEXT"
    NO_SOCIAL_ACCOUNT = "NO_SOCIAL_ACCOUNT"
    SOCIAL_ACCOUNT_DISABLED = "SOCIAL_ACCOUNT_DISABLED"
    TOKEN_MISSING = "TOKEN_MISSING"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    SUPABASE_AUTH_ERROR = "SUPABASE_AUTH_ERROR"
    SUPABASE_DATABASE_ERROR = "SUPABASE_DATABASE_ERROR"
    SUPABASE_STORAGE_ERROR = "SUPABASE_STORAGE_ERROR"
    RENDER_FAILED = "RENDER_FAILED"
    FACEBOOK_API_ERROR = "FACEBOOK_API_ERROR"
    FACEBOOK_PERMISSION_ERROR = "FACEBOOK_PERMISSION_ERROR"
    FACEBOOK_RATE_LIMITED = "FACEBOOK_RATE_LIMITED"
    FACEBOOK_NETWORK_ERROR = "FACEBOOK_NETWORK_ERROR"
    PLUGIN_NOT_STARTED = "PLUGIN_NOT_STARTED"
    CONTENT_EMPTY = "CONTENT_EMPTY"
    PUBLISH_FAILED = "PUBLISH_FAILED"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"


class PublishStatus(str, Enum):
    PENDING = "PENDING"
    GENERATING = "GENERATING"
    RENDERING = "RENDERING"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    RETRYING = "RETRYING"


@dataclass
class PublishingResult:
    """Structured result from any publishing operation."""
    ok: bool
    platform: str
    brand_id: Optional[str] = None
    account_id: Optional[str] = None
    platform_account_id: Optional[str] = None
    post_id: Optional[str] = None
    media_id: Optional[str] = None
    published_at: Optional[str] = None
    duration_ms: Optional[int] = None
    error_type: Optional[PublishErrorType] = None
    error_message: Optional[str] = None
    retryable: bool = False
    raw_response: Optional[dict] = field(default=None, repr=False)
    
    @classmethod
    def success(cls, platform: str, post_id: str, **kwargs) -> "PublishingResult":
        return cls(ok=True, platform=platform, post_id=post_id, **kwargs)
    
    @classmethod
    def failure(cls, platform: str, error_type: PublishErrorType, message: str, **kwargs) -> "PublishingResult":
        retryable = kwargs.pop("retryable", None)
        if retryable is None:
            retryable = error_type in (PublishErrorType.FACEBOOK_API_ERROR, PublishErrorType.UNKNOWN_ERROR)
        return cls(ok=False, platform=platform, error_type=error_type, error_message=message, retryable=retryable, **kwargs)
    
    def to_dict(self) -> dict:
        result = {
            "ok": self.ok,
            "platform": self.platform,
        }
        if self.brand_id:
            result["brand_id"] = self.brand_id
        if self.account_id:
            result["account_id"] = self.account_id
        if self.post_id:
            result["post_id"] = self.post_id
        if self.platform_account_id:
            result["platform_account_id"] = self.platform_account_id
        if self.duration_ms is not None:
            result["duration_ms"] = self.duration_ms
        if self.error_type:
            result["error"] = {
                "type": self.error_type.value,
                "message": self.error_message,
                "retryable": self.retryable,
            }
        return result
