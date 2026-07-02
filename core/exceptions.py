"""
Kanyoza Systems AI Platform v10 — Centralized exception hierarchy.

Rules:
  - All application exceptions inherit from KanyozaError.
  - Every exception carries a human-readable message and an optional error_code
    for consistent API responses.
  - Internal details (stack traces) are NEVER exposed to clients.
  - Callers catch KanyozaError sub-types; the API layer maps them to HTTP codes.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class KanyozaError(Exception):
    """
    Base class for all Kanyoza platform exceptions.

    Attributes
    ----------
    message : str
        Human-readable description (safe to log; may be shown in API errors).
    error_code : str
        Machine-readable code for client-side handling (e.g. "AI_TIMEOUT").
    context : dict
        Optional structured data attached to the error for logging.
    """

    error_code: str = "KANYOZA_ERROR"

    def __init__(
        self,
        message: str = "An unexpected error occurred.",
        error_code: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code or self.__class__.error_code
        self.context: Dict[str, Any] = context or {}

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for API error responses — no internal details."""
        return {"error": self.error_code, "message": self.message}


# ---------------------------------------------------------------------------
# Configuration errors — fail fast at startup
# ---------------------------------------------------------------------------


class ConfigurationError(KanyozaError):
    """Required configuration is missing or invalid."""
    error_code = "CONFIGURATION_ERROR"


class MissingSecretError(ConfigurationError):
    """A required secret / API key is not configured."""
    error_code = "MISSING_SECRET"

    def __init__(self, secret_name: str) -> None:
        super().__init__(
            message=f"Required secret '{secret_name}' is not configured.",
            context={"secret": secret_name},
        )


# ---------------------------------------------------------------------------
# AI / LLM errors
# ---------------------------------------------------------------------------


class AIError(KanyozaError):
    """Base for AI provider errors."""
    error_code = "AI_ERROR"


class AIProviderError(AIError):
    """The AI provider returned an error or unexpected response."""
    error_code = "AI_PROVIDER_ERROR"


class AITimeoutError(AIError):
    """AI provider did not respond within the timeout window."""
    error_code = "AI_TIMEOUT"


class AIParseError(AIError):
    """Failed to parse the AI provider's response into the expected model."""
    error_code = "AI_PARSE_ERROR"


class AIRateLimitError(AIError):
    """AI provider rate limit exceeded."""
    error_code = "AI_RATE_LIMIT"


class AIContentFilterError(AIError):
    """AI provider rejected content due to safety filters."""
    error_code = "AI_CONTENT_FILTERED"


# ---------------------------------------------------------------------------
# Facebook / Platform errors
# ---------------------------------------------------------------------------


class PlatformError(KanyozaError):
    """Base for social platform integration errors."""
    error_code = "PLATFORM_ERROR"


class FacebookError(PlatformError):
    """Facebook Graph API returned an error."""
    error_code = "FACEBOOK_ERROR"


class WebhookSignatureError(PlatformError):
    """Webhook payload signature verification failed."""
    error_code = "WEBHOOK_SIGNATURE_INVALID"


class MessageDeliveryError(PlatformError):
    """Failed to deliver a message to a user."""
    error_code = "MESSAGE_DELIVERY_FAILED"


# ---------------------------------------------------------------------------
# Memory / persistence errors
# ---------------------------------------------------------------------------


class MemoryError(KanyozaError):
    """Base for memory system errors."""
    error_code = "MEMORY_ERROR"


class MemoryReadError(MemoryError):
    """Failed to read from the memory store."""
    error_code = "MEMORY_READ_ERROR"


class MemoryWriteError(MemoryError):
    """Failed to write to the memory store."""
    error_code = "MEMORY_WRITE_ERROR"


# ---------------------------------------------------------------------------
# Rendering errors
# ---------------------------------------------------------------------------


class RenderError(KanyozaError):
    """Base for image / template rendering errors."""
    error_code = "RENDER_ERROR"


class TemplateNotFoundError(RenderError):
    """The requested Jinja2 template does not exist."""
    error_code = "TEMPLATE_NOT_FOUND"


class PlaywrightError(RenderError):
    """Playwright browser rendering failed."""
    error_code = "PLAYWRIGHT_ERROR"


# ---------------------------------------------------------------------------
# Scheduler errors
# ---------------------------------------------------------------------------


class SchedulerError(KanyozaError):
    """Base for scheduler errors."""
    error_code = "SCHEDULER_ERROR"


class JobNotFoundError(SchedulerError):
    """The requested job ID does not exist."""
    error_code = "JOB_NOT_FOUND"


class JobExecutionError(SchedulerError):
    """A scheduled job raised an exception during execution."""
    error_code = "JOB_EXECUTION_ERROR"


# ---------------------------------------------------------------------------
# Guardian errors
# ---------------------------------------------------------------------------


class GuardianError(KanyozaError):
    """Base for Code Guardian errors."""
    error_code = "GUARDIAN_ERROR"


class RepositoryAccessError(GuardianError):
    """Cannot access or clone the target repository."""
    error_code = "REPOSITORY_ACCESS_ERROR"


# ---------------------------------------------------------------------------
# API / request errors
# ---------------------------------------------------------------------------


class APIError(KanyozaError):
    """Base for REST API input/validation errors."""
    error_code = "API_ERROR"

    def __init__(
        self,
        message: str = "Bad request.",
        error_code: Optional[str] = None,
        http_status: int = 400,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message, error_code, context)
        self.http_status = http_status


class NotFoundError(APIError):
    """Requested resource does not exist."""
    error_code = "NOT_FOUND"

    def __init__(self, resource: str = "Resource") -> None:
        super().__init__(
            message=f"{resource} not found.",
            error_code="NOT_FOUND",
            http_status=404,
        )


class ValidationError(APIError):
    """Request payload failed schema validation."""
    error_code = "VALIDATION_ERROR"

    def __init__(self, detail: str = "Invalid request data.") -> None:
        super().__init__(message=detail, error_code="VALIDATION_ERROR", http_status=422)


class RateLimitError(APIError):
    """Client has exceeded the allowed request rate."""
    error_code = "RATE_LIMIT_EXCEEDED"

    def __init__(self) -> None:
        super().__init__(
            message="Rate limit exceeded. Please slow down.",
            error_code="RATE_LIMIT_EXCEEDED",
            http_status=429,
        )


class UnauthorizedError(APIError):
    """Request lacks valid authentication."""
    error_code = "UNAUTHORIZED"

    def __init__(self) -> None:
        super().__init__(
            message="Unauthorized.",
            error_code="UNAUTHORIZED",
            http_status=401,
        )
