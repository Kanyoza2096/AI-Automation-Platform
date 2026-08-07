"""
Centralised error taxonomy for Kanyoza AI Automation Platform.

Architecture §12 — Explicit Error Taxonomy:
  - Every error has a category, code, message, and retryable flag.
  - None is never an error model (§28).
  - Database failures are distinct from "not found" (§9).

Usage:
  from core.errors import KanyozaError, ErrorCategory, raise_if_none
  
  # Raise
  raise DatabaseUnavailableError("Supabase connection refused")
  
  # Return structured (for API/plugin boundaries)
  return KanyozaError.to_dict()
  
  # Guard against None
  account = raise_if_none(account, "Social account not found")
"""

from __future__ import annotations

import enum
from typing import Any, Optional


# ═══════════════════════════════════════════════════════════════════════════
# Error Category Enum
# ═══════════════════════════════════════════════════════════════════════════


class ErrorCategory(str, enum.Enum):
    """Top-level error categories matching Architecture §12."""
    
    # Infrastructure
    DATABASE = "DATABASE"
    STORAGE = "STORAGE"
    NETWORK = "NETWORK"
    
    # Authentication & Authorization
    AUTH = "AUTH"
    AUTHORIZATION = "AUTHORIZATION"
    
    # External Platforms
    PLATFORM = "PLATFORM"
    
    # AI Provider
    AI_PROVIDER = "AI_PROVIDER"
    
    # Media / Rendering
    MEDIA = "MEDIA"
    
    # Application Logic
    APPLICATION = "APPLICATION"
    
    # Unknown / Unexpected
    UNKNOWN = "UNKNOWN"


# ═══════════════════════════════════════════════════════════════════════════
# Specific Error Codes
# ═══════════════════════════════════════════════════════════════════════════


class ErrorCode(str, enum.Enum):
    """Granular error codes. Every failure must map to one of these."""
    
    # ── Database ──────────────────────────────────────────────────────────
    DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"
    DATABASE_QUERY_ERROR = "DATABASE_QUERY_ERROR"
    DATABASE_AUTH_ERROR = "DATABASE_AUTH_ERROR"
    DATABASE_TIMEOUT = "DATABASE_TIMEOUT"
    
    # ── Storage ───────────────────────────────────────────────────────────
    STORAGE_UNAVAILABLE = "STORAGE_UNAVAILABLE"
    STORAGE_UPLOAD_FAILED = "STORAGE_UPLOAD_FAILED"
    
    # ── Network ───────────────────────────────────────────────────────────
    NETWORK_ERROR = "NETWORK_ERROR"
    NETWORK_TIMEOUT = "NETWORK_TIMEOUT"
    
    # ── Auth ──────────────────────────────────────────────────────────────
    TOKEN_MISSING = "TOKEN_MISSING"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    TOKEN_INVALID = "TOKEN_INVALID"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    
    # ── Authorization ─────────────────────────────────────────────────────
    PERMISSION_DENIED = "PERMISSION_DENIED"
    PAGE_ACCESS_DENIED = "PAGE_ACCESS_DENIED"
    WORKSPACE_ACCESS_DENIED = "WORKSPACE_ACCESS_DENIED"
    BRAND_ACCESS_DENIED = "BRAND_ACCESS_DENIED"
    
    # ── Platform ──────────────────────────────────────────────────────────
    PLATFORM_RATE_LIMITED = "PLATFORM_RATE_LIMITED"
    PLATFORM_VALIDATION_ERROR = "PLATFORM_VALIDATION_ERROR"
    PLATFORM_UNAVAILABLE = "PLATFORM_UNAVAILABLE"
    FACEBOOK_API_ERROR = "FACEBOOK_API_ERROR"
    FACEBOOK_PERMISSION_ERROR = "FACEBOOK_PERMISSION_ERROR"
    
    # ── AI Provider ───────────────────────────────────────────────────────
    AI_TIMEOUT = "AI_TIMEOUT"
    AI_PROVIDER_ERROR = "AI_PROVIDER_ERROR"
    AI_INVALID_RESPONSE = "AI_INVALID_RESPONSE"
    AI_VALIDATION_ERROR = "AI_VALIDATION_ERROR"
    
    # ── Media ─────────────────────────────────────────────────────────────
    RENDER_FAILED = "RENDER_FAILED"
    MEDIA_INVALID = "MEDIA_INVALID"
    MEDIA_TOO_LARGE = "MEDIA_TOO_LARGE"
    
    # ── Application ───────────────────────────────────────────────────────
    NO_BRAND_CONTEXT = "NO_BRAND_CONTEXT"
    NO_SOCIAL_ACCOUNT = "NO_SOCIAL_ACCOUNT"
    ACCOUNT_DISABLED = "ACCOUNT_DISABLED"
    INVALID_JOB = "INVALID_JOB"
    DUPLICATE_JOB = "DUPLICATE_JOB"
    CONTENT_EMPTY = "CONTENT_EMPTY"
    PLUGIN_NOT_STARTED = "PLUGIN_NOT_STARTED"
    JOB_LOCKED = "JOB_LOCKED"
    STALE_JOB = "STALE_JOB"
    
    # ── Unknown ───────────────────────────────────────────────────────────
    UNKNOWN_ERROR = "UNKNOWN_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


# ═══════════════════════════════════════════════════════════════════════════
# Category mapping for each error code
# ═══════════════════════════════════════════════════════════════════════════

_ERROR_CATEGORY_MAP: dict[ErrorCode, ErrorCategory] = {
    ErrorCode.DATABASE_UNAVAILABLE: ErrorCategory.DATABASE,
    ErrorCode.DATABASE_QUERY_ERROR: ErrorCategory.DATABASE,
    ErrorCode.DATABASE_AUTH_ERROR: ErrorCategory.DATABASE,
    ErrorCode.DATABASE_TIMEOUT: ErrorCategory.DATABASE,
    ErrorCode.STORAGE_UNAVAILABLE: ErrorCategory.STORAGE,
    ErrorCode.STORAGE_UPLOAD_FAILED: ErrorCategory.STORAGE,
    ErrorCode.NETWORK_ERROR: ErrorCategory.NETWORK,
    ErrorCode.NETWORK_TIMEOUT: ErrorCategory.NETWORK,
    ErrorCode.TOKEN_MISSING: ErrorCategory.AUTH,
    ErrorCode.TOKEN_EXPIRED: ErrorCategory.AUTH,
    ErrorCode.TOKEN_INVALID: ErrorCategory.AUTH,
    ErrorCode.INVALID_CREDENTIALS: ErrorCategory.AUTH,
    ErrorCode.PERMISSION_DENIED: ErrorCategory.AUTHORIZATION,
    ErrorCode.PAGE_ACCESS_DENIED: ErrorCategory.AUTHORIZATION,
    ErrorCode.WORKSPACE_ACCESS_DENIED: ErrorCategory.AUTHORIZATION,
    ErrorCode.BRAND_ACCESS_DENIED: ErrorCategory.AUTHORIZATION,
    ErrorCode.PLATFORM_RATE_LIMITED: ErrorCategory.PLATFORM,
    ErrorCode.PLATFORM_VALIDATION_ERROR: ErrorCategory.PLATFORM,
    ErrorCode.PLATFORM_UNAVAILABLE: ErrorCategory.PLATFORM,
    ErrorCode.FACEBOOK_API_ERROR: ErrorCategory.PLATFORM,
    ErrorCode.FACEBOOK_PERMISSION_ERROR: ErrorCategory.PLATFORM,
    ErrorCode.AI_TIMEOUT: ErrorCategory.AI_PROVIDER,
    ErrorCode.AI_PROVIDER_ERROR: ErrorCategory.AI_PROVIDER,
    ErrorCode.AI_INVALID_RESPONSE: ErrorCategory.AI_PROVIDER,
    ErrorCode.AI_VALIDATION_ERROR: ErrorCategory.AI_PROVIDER,
    ErrorCode.RENDER_FAILED: ErrorCategory.MEDIA,
    ErrorCode.MEDIA_INVALID: ErrorCategory.MEDIA,
    ErrorCode.MEDIA_TOO_LARGE: ErrorCategory.MEDIA,
    ErrorCode.NO_BRAND_CONTEXT: ErrorCategory.APPLICATION,
    ErrorCode.NO_SOCIAL_ACCOUNT: ErrorCategory.APPLICATION,
    ErrorCode.ACCOUNT_DISABLED: ErrorCategory.APPLICATION,
    ErrorCode.INVALID_JOB: ErrorCategory.APPLICATION,
    ErrorCode.DUPLICATE_JOB: ErrorCategory.APPLICATION,
    ErrorCode.CONTENT_EMPTY: ErrorCategory.APPLICATION,
    ErrorCode.PLUGIN_NOT_STARTED: ErrorCategory.APPLICATION,
    ErrorCode.JOB_LOCKED: ErrorCategory.APPLICATION,
    ErrorCode.STALE_JOB: ErrorCategory.APPLICATION,
    ErrorCode.UNKNOWN_ERROR: ErrorCategory.UNKNOWN,
    ErrorCode.INTERNAL_ERROR: ErrorCategory.UNKNOWN,
}


# ═══════════════════════════════════════════════════════════════════════════
# Base Error Class
# ═══════════════════════════════════════════════════════════════════════════


class KanyozaError(Exception):
    """Base exception for all Kanyoza operational errors.
    
    Every error carries:
      - code: Machine-readable error code (ErrorCode enum)
      - category: Top-level category (ErrorCategory enum)
      - message: Human-readable description
      - retryable: Whether the operation can be safely retried
      - context: Optional dict with additional diagnostic information
      - cause: Optional underlying exception
    
    Architecture §28: A failure should answer:
      WHAT failed? (category)
      WHERE did it fail? (context)
      WHY did it fail? (message)
      IS it retryable? (retryable)
    """
    
    def __init__(
        self,
        code: ErrorCode,
        message: str = "",
        *,
        retryable: bool = False,
        context: Optional[dict[str, Any]] = None,
        cause: Optional[Exception] = None,
    ) -> None:
        self.code = code
        self.category = _ERROR_CATEGORY_MAP.get(code, ErrorCategory.UNKNOWN)
        self.message = message or code.value
        self.retryable = retryable
        self.context = context or {}
        self.cause = cause
        super().__init__(self._format_message())
    
    def _format_message(self) -> str:
        base = f"[{self.code.value}] {self.message}"
        if self.cause:
            base += f" (caused by: {type(self.cause).__name__}: {self.cause})"
        return base
    
    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dictionary safe for API responses and logging.
        
        Compatible with the existing PublishingResult.to_dict() pattern.
        """
        result: dict[str, Any] = {
            "ok": False,
            "error": {
                "code": self.code.value,
                "category": self.category.value,
                "message": self.message,
                "retryable": self.retryable,
            },
        }
        if self.context:
            result["context"] = self.context
        return result
    
    @classmethod
    def from_exception(
        cls,
        exc: Exception,
        code: ErrorCode = ErrorCode.INTERNAL_ERROR,
        message: str = "",
        *,
        retryable: bool = False,
    ) -> "KanyozaError":
        """Wrap an existing exception as a KanyozaError."""
        return cls(
            code=code,
            message=message or str(exc),
            retryable=retryable,
            cause=exc,
        )


# ═══════════════════════════════════════════════════════════════════════════
# Specific Error Subclasses
# ═══════════════════════════════════════════════════════════════════════════


# ── Database ──────────────────────────────────────────────────────────────

class DatabaseError(KanyozaError):
    """Base for all database-related errors."""
    def __init__(self, code: ErrorCode, message: str = "", **kwargs):
        super().__init__(code, message, **kwargs)


class DatabaseUnavailableError(DatabaseError):
    """Database could not be reached."""
    def __init__(self, message: str = "Database unavailable", **kwargs):
        super().__init__(ErrorCode.DATABASE_UNAVAILABLE, message, retryable=True, **kwargs)


class DatabaseQueryError(DatabaseError):
    """A database query failed."""
    def __init__(self, message: str = "Database query failed", **kwargs):
        super().__init__(ErrorCode.DATABASE_QUERY_ERROR, message, **kwargs)


class DatabaseAuthError(DatabaseError):
    """Database authentication/authorization failed."""
    def __init__(self, message: str = "Database authentication failed", **kwargs):
        super().__init__(ErrorCode.DATABASE_AUTH_ERROR, message, retryable=False, **kwargs)


# ── Auth ──────────────────────────────────────────────────────────────────

class AuthError(KanyozaError):
    """Base for authentication errors."""
    def __init__(self, code: ErrorCode, message: str = "", **kwargs):
        super().__init__(code, message, **kwargs)


class TokenMissingError(AuthError):
    def __init__(self, message: str = "No credentials available", **kwargs):
        super().__init__(ErrorCode.TOKEN_MISSING, message, retryable=False, **kwargs)


class TokenExpiredError(AuthError):
    def __init__(self, message: str = "Token has expired", **kwargs):
        super().__init__(ErrorCode.TOKEN_EXPIRED, message, retryable=False, **kwargs)


# ── Platform ──────────────────────────────────────────────────────────────

class PlatformError(KanyozaError):
    """Base for external platform errors."""
    def __init__(self, code: ErrorCode, message: str = "", **kwargs):
        super().__init__(code, message, **kwargs)


class PlatformRateLimitedError(PlatformError):
    def __init__(self, message: str = "Platform rate limit exceeded", **kwargs):
        super().__init__(ErrorCode.PLATFORM_RATE_LIMITED, message, retryable=True, **kwargs)


class FacebookAPIError(PlatformError):
    def __init__(self, message: str = "Facebook API error", **kwargs):
        super().__init__(ErrorCode.FACEBOOK_API_ERROR, message, **kwargs)


# ── AI Provider ───────────────────────────────────────────────────────────

class AIProviderError(KanyozaError):
    """Base for AI provider errors."""
    def __init__(self, code: ErrorCode, message: str = "", **kwargs):
        super().__init__(code, message, **kwargs)


class AITimeoutError(AIProviderError):
    def __init__(self, message: str = "AI provider timed out", **kwargs):
        super().__init__(ErrorCode.AI_TIMEOUT, message, retryable=True, **kwargs)


class AIInvalidResponseError(AIProviderError):
    def __init__(self, message: str = "AI returned invalid response", **kwargs):
        super().__init__(ErrorCode.AI_INVALID_RESPONSE, message, retryable=True, **kwargs)


class AIValidationError(AIProviderError):
    """AI output failed content validation.
    
    Architecture §21-22: Raised when AI-generated content fails
    schema validation, brand constraints, or content policy checks.
    """
    def __init__(self, message: str = "AI output validation failed", **kwargs):
        super().__init__(ErrorCode.AI_VALIDATION_ERROR, message, retryable=False, **kwargs)


# ── Media ─────────────────────────────────────────────────────────────────

class MediaError(KanyozaError):
    """Base for media/rendering errors."""
    def __init__(self, code: ErrorCode, message: str = "", **kwargs):
        super().__init__(code, message, **kwargs)


class RenderFailedError(MediaError):
    def __init__(self, message: str = "Image rendering failed", **kwargs):
        super().__init__(ErrorCode.RENDER_FAILED, message, retryable=True, **kwargs)


# ── Application ───────────────────────────────────────────────────────────

class ApplicationError(KanyozaError):
    """Base for application-level errors."""
    def __init__(self, code: ErrorCode, message: str = "", **kwargs):
        super().__init__(code, message, **kwargs)


class NoBrandContextError(ApplicationError):
    def __init__(self, message: str = "No brand context available", **kwargs):
        super().__init__(ErrorCode.NO_BRAND_CONTEXT, message, retryable=False, **kwargs)


class NoSocialAccountError(ApplicationError):
    def __init__(self, message: str = "No social account found", **kwargs):
        super().__init__(ErrorCode.NO_SOCIAL_ACCOUNT, message, retryable=False, **kwargs)


class DuplicateJobError(ApplicationError):
    def __init__(self, message: str = "Job already exists", **kwargs):
        super().__init__(ErrorCode.DUPLICATE_JOB, message, retryable=False, **kwargs)


# ═══════════════════════════════════════════════════════════════════════════
# Helper Functions
# ═══════════════════════════════════════════════════════════════════════════


def raise_if_none(
    value: Any,
    message: str,
    error_cls: type[KanyozaError] = ApplicationError,
    code: ErrorCode = ErrorCode.INTERNAL_ERROR,
    **kwargs,
) -> Any:
    """Raise if value is None. Returns value otherwise.
    
    Eliminates the pattern:
        if thing is None:
            return None  # or silently fall through
    
    Usage:
        account = raise_if_none(
            repo.find_by_brand(brand_id),
            f"No social account for brand {brand_id}",
            NoSocialAccountError,
        )
    """
    if value is None:
        raise error_cls(code=code, message=message, **kwargs)
    return value


def is_retryable(error: Exception) -> bool:
    """Check if an error is retryable.
    
    Works with KanyozaError and common built-in exceptions.
    """
    if isinstance(error, KanyozaError):
        return error.retryable
    
    # Common retryable exceptions
    if isinstance(error, (ConnectionError, TimeoutError)):
        return True
    
    # HTTP 5xx equivalents
    error_str = str(error).lower()
    retryable_patterns = [
        "timeout", "connection", "unavailable", "rate limit",
        "500", "502", "503", "504", "temporary",
    ]
    return any(pattern in error_str for pattern in retryable_patterns)
