"""
Centralised error taxonomy for Kanyoza AI Automation Platform.

Architecture §12 — Explicit Error Taxonomy:
  - Every error has a category, code, message, and retryable flag.
  - None is never an error model (§28).
  - Database failures are distinct from "not found" (§9).

v2 changes (hardening):
  - str(KanyozaError) no longer embeds the cause. Prevents secret leakage
    into logs and tracebacks.
  - to_dict() filters context by default (public=True).
  - Context keys are validated at construction; secret-named keys rejected.
  - raise_if_none works correctly with fixed-code subclasses.
  - is_retryable uses type checks instead of string matching.
  - Exceptions are picklable.
  - Category map exhaustiveness is asserted at import.

Usage:
  from core.errors import KanyozaError, ErrorCategory, raise_if_none

  # Raise
  raise DatabaseUnavailableError(message="Supabase connection refused")

  # Serialize for API boundary (safe context only)
  return err.to_dict()             # public=True (default)
  return err.to_dict(public=False) # internal logging only — DO NOT send over HTTP

  # Guard against None
  account = raise_if_none(account, "Social account not found", NoSocialAccountError)
"""

from __future__ import annotations

import asyncio
import enum
from typing import Any, Final, Optional, TypeVar

from core.redaction import sanitize_error_message

# Optional httpx import — is_retryable uses it if available.
try:
    import httpx  # type: ignore
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore


T = TypeVar("T")


# ═══════════════════════════════════════════════════════════════════════════
# Error Category Enum
# ═══════════════════════════════════════════════════════════════════════════


class ErrorCategory(str, enum.Enum):
    """Top-level error categories matching Architecture §12."""

    DATABASE = "DATABASE"
    STORAGE = "STORAGE"
    NETWORK = "NETWORK"
    AUTH = "AUTH"
    AUTHORIZATION = "AUTHORIZATION"
    PLATFORM = "PLATFORM"
    AI_PROVIDER = "AI_PROVIDER"
    MEDIA = "MEDIA"
    APPLICATION = "APPLICATION"
    UNKNOWN = "UNKNOWN"


# ═══════════════════════════════════════════════════════════════════════════
# Specific Error Codes
# ═══════════════════════════════════════════════════════════════════════════


class ErrorCode(str, enum.Enum):
    """Granular error codes. Every failure must map to one of these."""

    # Database
    DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"
    DATABASE_QUERY_ERROR = "DATABASE_QUERY_ERROR"
    DATABASE_AUTH_ERROR = "DATABASE_AUTH_ERROR"
    DATABASE_TIMEOUT = "DATABASE_TIMEOUT"

    # Storage
    STORAGE_UNAVAILABLE = "STORAGE_UNAVAILABLE"
    STORAGE_UPLOAD_FAILED = "STORAGE_UPLOAD_FAILED"

    # Network
    NETWORK_ERROR = "NETWORK_ERROR"
    NETWORK_TIMEOUT = "NETWORK_TIMEOUT"

    # Auth
    TOKEN_MISSING = "TOKEN_MISSING"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    TOKEN_INVALID = "TOKEN_INVALID"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"

    # Authorization
    PERMISSION_DENIED = "PERMISSION_DENIED"
    PAGE_ACCESS_DENIED = "PAGE_ACCESS_DENIED"
    WORKSPACE_ACCESS_DENIED = "WORKSPACE_ACCESS_DENIED"
    BRAND_ACCESS_DENIED = "BRAND_ACCESS_DENIED"

    # Platform
    PLATFORM_RATE_LIMITED = "PLATFORM_RATE_LIMITED"
    PLATFORM_VALIDATION_ERROR = "PLATFORM_VALIDATION_ERROR"
    PLATFORM_UNAVAILABLE = "PLATFORM_UNAVAILABLE"
    FACEBOOK_API_ERROR = "FACEBOOK_API_ERROR"
    FACEBOOK_PERMISSION_ERROR = "FACEBOOK_PERMISSION_ERROR"

    # AI Provider
    AI_TIMEOUT = "AI_TIMEOUT"
    AI_PROVIDER_ERROR = "AI_PROVIDER_ERROR"
    AI_INVALID_RESPONSE = "AI_INVALID_RESPONSE"
    AI_VALIDATION_ERROR = "AI_VALIDATION_ERROR"

    # Media
    RENDER_FAILED = "RENDER_FAILED"
    MEDIA_INVALID = "MEDIA_INVALID"
    MEDIA_TOO_LARGE = "MEDIA_TOO_LARGE"

    # Application
    NO_BRAND_CONTEXT = "NO_BRAND_CONTEXT"
    NO_SOCIAL_ACCOUNT = "NO_SOCIAL_ACCOUNT"
    ACCOUNT_DISABLED = "ACCOUNT_DISABLED"
    INVALID_JOB = "INVALID_JOB"
    DUPLICATE_JOB = "DUPLICATE_JOB"
    CONTENT_EMPTY = "CONTENT_EMPTY"
    PLUGIN_NOT_STARTED = "PLUGIN_NOT_STARTED"
    JOB_LOCKED = "JOB_LOCKED"
    STALE_JOB = "STALE_JOB"

    # Unknown
    UNKNOWN_ERROR = "UNKNOWN_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


# ═══════════════════════════════════════════════════════════════════════════
# Category mapping
# ═══════════════════════════════════════════════════════════════════════════


_ERROR_CATEGORY_MAP: Final[dict[ErrorCode, ErrorCategory]] = {
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


# Exhaustiveness check — fails at import if a new code is added without a mapping.
assert set(_ERROR_CATEGORY_MAP.keys()) == set(ErrorCode), (
    "ErrorCode/Category map mismatch. Unmapped: "
    f"{set(ErrorCode) - set(_ERROR_CATEGORY_MAP.keys())}"
)


# ═══════════════════════════════════════════════════════════════════════════
# Context key policy
# ═══════════════════════════════════════════════════════════════════════════


# Keys allowed to appear in API responses when to_dict(public=True).
# Everything else is stripped. Add to this list deliberately; do not add
# "convenience" keys that might carry sensitive values.
_PUBLIC_CONTEXT_KEYS: Final[frozenset[str]] = frozenset({
    "operation",
    "table",
    "workspace_id",
    "brand_id",
    "job_id",
    "account_id",
    "client_type",
    "retry_after",
    "attempt",
    "status_code",
    "endpoint",
    "rule_id",
    "plugin",
    "phase",
    "step",
})


# Keys rejected at construction time. Rejecting is better than silently
# stripping later — it forces the call site to fix the mistake.
_FORBIDDEN_CONTEXT_KEYS: Final[frozenset[str]] = frozenset({
    "key", "token", "secret", "password", "apikey", "api_key",
    "authorization", "auth", "jwt", "bearer", "credential", "credentials",
    "service_role_key", "anon_key", "supabase_key", "private_key",
    "cookie", "set-cookie", "session", "session_id",
})


def _validate_context(context: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Copy and validate context. Raises ValueError for forbidden keys."""
    if not context:
        return {}
    copied = dict(context)
    for key in copied.keys():
        if isinstance(key, str) and key.lower() in _FORBIDDEN_CONTEXT_KEYS:
            raise ValueError(
                f"KanyozaError context key {key!r} is forbidden: never place "
                f"secrets in error context. Redact at the call site."
            )
    return copied


# ═══════════════════════════════════════════════════════════════════════════
# Base Error Class
# ═══════════════════════════════════════════════════════════════════════════


class KanyozaError(Exception):
    """Base exception for all Kanyoza operational errors.

    Every error carries:
      - code: machine-readable error code (ErrorCode)
      - category: top-level category (ErrorCategory)
      - message: human-readable description
      - retryable: whether the operation can be safely retried
      - context: optional dict with diagnostic information
      - cause: optional underlying exception

    Contract:
      - str(err) does NOT include the cause. Use err.cause for structured
        access, and sanitize before logging.
      - context keys are validated at construction; secret-named keys are
        rejected with ValueError.
      - to_dict() filters context by default. Use to_dict(public=False) for
        internal logging only — never send it over the wire.
      - Do NOT wrap asyncio.CancelledError, KeyboardInterrupt, or SystemExit.
    """

    # Subclasses override via super().__init__(code=...). Base default:
    _default_code: ErrorCode = ErrorCode.INTERNAL_ERROR
    _default_retryable: bool = False

    def __init__(
        self,
        message: str = "",
        *,
        code: Optional[ErrorCode] = None,
        retryable: Optional[bool] = None,
        context: Optional[dict[str, Any]] = None,
        cause: Optional[BaseException] = None,
    ) -> None:
        resolved_code = code if code is not None else self._default_code
        self.code: ErrorCode = resolved_code
        self.category: ErrorCategory = _ERROR_CATEGORY_MAP.get(
            resolved_code, ErrorCategory.UNKNOWN
        )
        self.message: str = message or resolved_code.value
        self.retryable: bool = (
            retryable if retryable is not None else self._default_retryable
        )
        self.context: dict[str, Any] = _validate_context(context)
        self.cause: Optional[BaseException] = cause
        super().__init__(self._format_message())
        # Deliberately do NOT set __cause__ — see module docstring.

    def _format_message(self) -> str:
        """Message used for str(err). Never includes the cause."""
        return f"[{self.code.value}] {self.message}"

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}("
            f"code={self.code.value!r}, message={self.message!r}, "
            f"retryable={self.retryable!r})"
        )

    def to_dict(self, *, public: bool = True) -> dict[str, Any]:
        """Serialize for API responses (public=True) or internal logs (public=False).

        public=True: only context keys in _PUBLIC_CONTEXT_KEYS survive.
        public=False: full context. NEVER send this over the wire.
        """
        error: dict[str, Any] = {
            "code": self.code.value,
            "category": self.category.value,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.context:
            if public:
                filtered = {
                    k: v for k, v in self.context.items()
                    if k in _PUBLIC_CONTEXT_KEYS
                }
                if filtered:
                    error["context"] = filtered
            else:
                error["context"] = self.context
        return {"ok": False, "error": error}

    def log_dict(self) -> dict[str, Any]:
        """Structured logging payload. Safe to pass to logger `extra=`."""
        return {
            "error_code": self.code.value,
            "error_category": self.category.value,
            "error_message": self.message,
            "error_retryable": self.retryable,
            "error_context_keys": list(self.context.keys()),
            "error_cause_type": (
                type(self.cause).__name__ if self.cause is not None else None
            ),
        }

    @classmethod
    def from_exception(
        cls,
        exc: BaseException,
        code: Optional[ErrorCode] = None,
        message: str = "",
        *,
        retryable: Optional[bool] = None,
    ) -> "KanyozaError":
        """Wrap an existing exception. Cause string is sanitized."""
        safe_message = message or sanitize_error_message(exc)
        kwargs: dict[str, Any] = {"message": safe_message, "cause": exc}
        if code is not None:
            kwargs["code"] = code
        if retryable is not None:
            kwargs["retryable"] = retryable
        return cls(**kwargs)

    def __reduce__(self) -> tuple:
        return (
            _reconstruct_error,
            (
                type(self),
                self.code,
                self.message,
                self.retryable,
                self.context,
            ),
        )


def _reconstruct_error(
    error_cls: type[KanyozaError],
    code: ErrorCode,
    message: str,
    retryable: bool,
    context: dict[str, Any],
) -> KanyozaError:
    """Pickle reconstruction helper. Cause is not preserved across pickle."""
    return error_cls(
        message=message, code=code, retryable=retryable, context=context
    )


# ═══════════════════════════════════════════════════════════════════════════
# Subclasses
# ═══════════════════════════════════════════════════════════════════════════


class DatabaseError(KanyozaError):
    _default_code = ErrorCode.DATABASE_QUERY_ERROR


class DatabaseUnavailableError(DatabaseError):
    _default_code = ErrorCode.DATABASE_UNAVAILABLE
    _default_retryable = True


class DatabaseQueryError(DatabaseError):
    _default_code = ErrorCode.DATABASE_QUERY_ERROR


class DatabaseAuthError(DatabaseError):
    _default_code = ErrorCode.DATABASE_AUTH_ERROR
    _default_retryable = False


class AuthError(KanyozaError):
    _default_code = ErrorCode.INVALID_CREDENTIALS


class TokenMissingError(AuthError):
    _default_code = ErrorCode.TOKEN_MISSING


class TokenExpiredError(AuthError):
    _default_code = ErrorCode.TOKEN_EXPIRED


class PlatformError(KanyozaError):
    _default_code = ErrorCode.PLATFORM_UNAVAILABLE


class PlatformRateLimitedError(PlatformError):
    _default_code = ErrorCode.PLATFORM_RATE_LIMITED
    _default_retryable = True


class FacebookAPIError(PlatformError):
    _default_code = ErrorCode.FACEBOOK_API_ERROR


class AIProviderError(KanyozaError):
    _default_code = ErrorCode.AI_PROVIDER_ERROR


class AITimeoutError(AIProviderError):
    _default_code = ErrorCode.AI_TIMEOUT
    _default_retryable = True


class AIInvalidResponseError(AIProviderError):
    _default_code = ErrorCode.AI_INVALID_RESPONSE
    _default_retryable = True


class AIValidationError(AIProviderError):
    _default_code = ErrorCode.AI_VALIDATION_ERROR
    _default_retryable = False


class MediaError(KanyozaError):
    _default_code = ErrorCode.RENDER_FAILED


class RenderFailedError(MediaError):
    _default_code = ErrorCode.RENDER_FAILED
    _default_retryable = True


class ApplicationError(KanyozaError):
    _default_code = ErrorCode.INTERNAL_ERROR


class NoBrandContextError(ApplicationError):
    _default_code = ErrorCode.NO_BRAND_CONTEXT


class NoSocialAccountError(ApplicationError):
    _default_code = ErrorCode.NO_SOCIAL_ACCOUNT


class DuplicateJobError(ApplicationError):
    _default_code = ErrorCode.DUPLICATE_JOB


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


def raise_if_none(
    value: Optional[T],
    message: str,
    error_cls: type[KanyozaError] = ApplicationError,
    *,
    code: Optional[ErrorCode] = None,
    **kwargs: Any,
) -> T:
    """Raise if value is None; otherwise return it, narrowed to T.

    Works with any KanyozaError subclass, including those with fixed codes.

    Usage:
        account = raise_if_none(
            repo.find_by_brand(brand_id),
            f"No social account for brand {brand_id}",
            NoSocialAccountError,
            context={"brand_id": brand_id},
        )
    """
    if value is None:
        if code is not None:
            kwargs["code"] = code
        raise error_cls(message=message, **kwargs)
    return value


def is_retryable(error: BaseException) -> bool:
    """Return True if the error is safe to retry.

    Type-based. Never string-matches message content.
    """
    if isinstance(error, KanyozaError):
        return error.retryable

    if isinstance(error, (ConnectionError, TimeoutError, asyncio.TimeoutError)):
        return True

    if httpx is not None:
        if isinstance(error, httpx.TimeoutException):
            return True
        if isinstance(error, httpx.ConnectError):
            return True
        if isinstance(error, httpx.RemoteProtocolError):
            return True
        if isinstance(error, httpx.HTTPStatusError):
            try:
                return error.response.status_code in {408, 425, 429, 500, 502, 503, 504}
            except Exception:
                return False

    return False


__all__ = [
    "ErrorCategory",
    "ErrorCode",
    "KanyozaError",
    "DatabaseError",
    "DatabaseUnavailableError",
    "DatabaseQueryError",
    "DatabaseAuthError",
    "AuthError",
    "TokenMissingError",
    "TokenExpiredError",
    "PlatformError",
    "PlatformRateLimitedError",
    "FacebookAPIError",
    "AIProviderError",
    "AITimeoutError",
    "AIInvalidResponseError",
    "AIValidationError",
    "MediaError",
    "RenderFailedError",
    "ApplicationError",
    "NoBrandContextError",
    "NoSocialAccountError",
    "DuplicateJobError",
    "raise_if_none",
    "is_retryable",
]
