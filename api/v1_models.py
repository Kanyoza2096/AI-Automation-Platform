from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Optional, List
import logging
import re
import html

# ═══════════════════════════════════════════════════════════════════════════
# PYDANTIC REQUEST MODELS — A03 Input validation at the boundary
# ═══════════════════════════════════════════════════════════════════════════

logger = logging.getLogger("kanyoza.api.v1_models")

# Local validation constants intentionally live in this model module so the
# request models do not depend on importing the monolithic api.v1 module.
MAX_AI_PROMPT_LENGTH = 4000
HTML_TAG_RE = re.compile(r"<[^>]+>")

# Prompt-injection patterns. Local to this module so the models are
# self-contained. If a shared pattern library emerges later, migrate here.
INJECTION_PATTERNS_RE = re.compile(
    r"(?i)\b("
    r"ignore (?:all )?(?:the )?(?:previous|above|prior|system) instructions"
    r"|disregard (?:all )?(?:the )?(?:previous|above|prior|system)"
    r"|you are now"
    r"|system:\s"
    r"|assistant:\s"
    r"|forget (?:all )?(?:your )?instructions"
    r")"
)

# Correlation id: prefer the canonical ContextVar from api.support so log
# entries from model validators join the request trace. Fall back to a local
# ContextVar if the import fails (defensive — the module must always import).
try:
    from api.support import correlation_id_var  # type: ignore  # noqa: F401
except Exception:  # pragma: no cover
    from contextvars import ContextVar
    correlation_id_var: ContextVar = ContextVar("correlation_id", default="unknown")


def _sanitize(value: Optional[str]) -> str:
    """Defensive string sanitizer used by every validator in this module.

    Contract:
      - None or "" → "" (never returns None; validators must return str)
      - Strips HTML tags (XSS defense at the model boundary)
      - Collapses whitespace runs and trims
      - Does NOT truncate — max_length is enforced by Field(), not here.
        Content length preservation is required for long-form text
        (knowledge base entries, sermon text, AI prompts).
    """
    if value is None:
        return ""
    s = str(value)
    s = HTML_TAG_RE.sub("", s)
    s = " ".join(s.split())
    return s


# ═══════════════════════════════════════════════════════════════════════════
# POSTS
# ═══════════════════════════════════════════════════════════════════════════


class PostCreateRequest(BaseModel):
    """A03: All string fields validated for length and content."""
    caption: str = Field(default="", max_length=5000)
    topic: str = Field(default="", max_length=500)
    category: str = Field(default="", max_length=200)
    state: str = Field(default="publish", pattern=r"^(publish|draft)$")
    # Fields required by Mutex frontend contract (spec §1.2)
    brand_id: Optional[str] = Field(default=None, max_length=128)
    platforms: Optional[list[str]] = Field(default=None, max_length=20)
    ai_generate: Optional[bool] = Field(default=None)
    scheduled_for: Optional[str] = Field(default=None, max_length=64)
    workspace_id: Optional[str] = Field(default=None, max_length=128)
    decision_id: Optional[str] = Field(default=None, max_length=256)
    idempotency_key: Optional[str] = Field(default=None, max_length=256)
    risk_level: str = Field(default="low", pattern=r"^(trivial|low|medium|high|critical)$")

    @field_validator("caption", "topic", "category")
    @classmethod
    def sanitize_strings(cls, v: str) -> str:
        return _sanitize(v)


# ═══════════════════════════════════════════════════════════════════════════
# MESSAGING
# ═══════════════════════════════════════════════════════════════════════════


class MessageReplyRequest(BaseModel):
    social_account_id: str = Field(..., min_length=1, max_length=128)
    recipient_id: str = Field(..., min_length=1, max_length=128)
    text: str = Field(..., min_length=1, max_length=2000)

    @field_validator("social_account_id", "recipient_id", "text")
    @classmethod
    def sanitize_message_fields(cls, v: str) -> str:
        return _sanitize(v)


# ═══════════════════════════════════════════════════════════════════════════
# AI
# ═══════════════════════════════════════════════════════════════════════════


class AIChatRequest(BaseModel):
    """A03: Prompt injection guard at the validation layer."""
    message: str = Field(..., min_length=1, max_length=MAX_AI_PROMPT_LENGTH)
    history: list[dict] = Field(default_factory=list, max_length=50)
    context: dict = Field(default_factory=dict)

    @field_validator("message")
    @classmethod
    def sanitize_and_check_injection(cls, v: str) -> str:
        clean = HTML_TAG_RE.sub("", str(v))
        clean = " ".join(clean.split())
        if not clean:
            raise ValueError("message must not be empty after sanitization")
        if INJECTION_PATTERNS_RE.search(clean):
            cid = correlation_id_var.get()
            logger.warning("[%s] A03: Prompt injection blocked in validator", cid)
            raise ValueError("Message contains prohibited patterns")
        # Escape HTML entities for downstream LLM context. Storage keeps raw
        # text; this field is destined for the model prompt, so escaping is
        # appropriate here.
        return html.escape(clean, quote=False)


class AIInterpretRequest(BaseModel):
    """Provider-neutral AI interpretation request. No action execution is implied."""
    task: str = Field(default="interpretation", max_length=64)
    message: str = Field(..., min_length=1, max_length=4000)
    system_prompt: str = Field(default="", max_length=5000)
    model: Optional[str] = Field(default=None, max_length=200)
    structured: bool = True
    workspace_id: Optional[str] = Field(default=None, max_length=128)

    @field_validator("message", "system_prompt")
    @classmethod
    def sanitize_ai_text(cls, v: str) -> str:
        return _sanitize(v)


class AIConfigUpdateRequest(BaseModel):
    provider: Optional[str] = Field(default=None, pattern=r"^(gemini|huggingface|openai|anthropic|cloudflare)$")
    model: Optional[str] = Field(default=None, max_length=128)
    chat_temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    post_temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    safety_level: Optional[str] = Field(default=None, pattern=r"^(low|medium|high)$")
    system_prompt_override: Optional[str] = Field(default=None, max_length=5000)
    persona_mood: Optional[str] = Field(default=None, max_length=64)
    tone_assertiveness: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    tone_humor: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    tone_formality: Optional[float] = Field(default=None, ge=0.0, le=1.0)


# ═══════════════════════════════════════════════════════════════════════════
# WORKSPACES + USERS
# ═══════════════════════════════════════════════════════════════════════════


class WorkspaceCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    id: Optional[str] = Field(default=None, max_length=64)
    slug: Optional[str] = Field(default=None, max_length=128)
    description: str = Field(default="", max_length=1000)
    owner: Optional[str] = Field(default=None, max_length=256, description="Owner email address")
    plan: Optional[str] = Field(default="free", pattern=r"^(free|pro|business|enterprise)$")

    @field_validator("name", "description")
    @classmethod
    def sanitize_workspace_fields(cls, v: str) -> str:
        return _sanitize(v)


class UserCreateRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=256)
    name: str = Field(..., min_length=1, max_length=256)
    role: str = Field(default="viewer", pattern=r"^(viewer|editor|admin|owner)$")
    workspace_id: Optional[str] = Field(default=None, max_length=128)

    @field_validator("email", "name")
    @classmethod
    def sanitize_user_fields(cls, v: str) -> str:
        return _sanitize(v)


class UserUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=256)
    role: Optional[str] = Field(default=None, pattern=r"^(viewer|editor|admin|owner)$")
    status: Optional[str] = Field(default=None, pattern=r"^(active|inactive)$")

    @field_validator("name")
    @classmethod
    def sanitize_user_name(cls, v: Optional[str]) -> Optional[str]:
        return _sanitize(v) if v else v


class UserDeleteRequest(BaseModel):
    reassign_to: Optional[str] = Field(default=None, max_length=255)


# ═══════════════════════════════════════════════════════════════════════════
# SCHEDULING + RATE LIMITS
# ═══════════════════════════════════════════════════════════════════════════


class ScheduleUpdateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    post_interval_hours: Optional[int] = Field(default=None, ge=1, le=168)
    news_interval_hours: Optional[int] = Field(default=None, ge=1, le=168)
    enabled: Optional[bool] = None
    content_hours_utc: Optional[list[int]] = None
    news_hours_utc: Optional[list[int]] = None

    @field_validator("content_hours_utc", "news_hours_utc")
    @classmethod
    def validate_hours(cls, v: list[int] | None) -> list[int] | None:
        if v is None:
            return v
        for hour in v:
            if not 0 <= hour <= 23:
                raise ValueError(f"Hour must be 0-23, got {hour}")
        return v


class RateLimitConfigRequest(BaseModel):
    limit: int = Field(..., ge=1, le=10000)
    window: int = Field(..., ge=1, le=86400, description="Window in seconds")
    scope: str = Field(default="global", max_length=64)
    workspace_id: Optional[str] = Field(default=None, max_length=128)
    provider: Optional[str] = Field(default=None, max_length=64)
    enabled: bool = Field(default=True)


# ═══════════════════════════════════════════════════════════════════════════
# IMAGE + API KEYS
# ═══════════════════════════════════════════════════════════════════════════


class ImageGenerateRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=500)
    content: Optional[str] = Field(default="", max_length=2000)
    author: Optional[str] = Field(default="")
    platform: Optional[str] = Field(default="facebook")


class APIKeyGenerateRequest(BaseModel):
    label: str = Field(default="api-key", min_length=1, max_length=64)
    scopes: list[str] = Field(default_factory=lambda: ["read"], min_length=1, max_length=2)
    # None means no scheduled expiry; explicit revocation remains available.
    expires_in_days: Optional[int] = Field(default=30, ge=1, le=365)

    @field_validator("label")
    @classmethod
    def sanitize_label(cls, v: str) -> str:
        return _sanitize(v)

    @field_validator("scopes")
    @classmethod
    def validate_scopes(cls, values: list[str]) -> list[str]:
        allowed = {"read", "write"}
        normalized = list(dict.fromkeys(str(value).strip().lower() for value in values))
        if not normalized or "read" not in normalized or not set(normalized).issubset(allowed):
            raise ValueError("scopes must include read and may additionally include write")
        return normalized


# ═══════════════════════════════════════════════════════════════════════════
# KNOWLEDGE
# ═══════════════════════════════════════════════════════════════════════════


class KnowledgeCreateRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=50000)
    doc_type: str = Field(default="text", max_length=64)
    metadata: dict = Field(default_factory=dict)
    title: str = Field(default="", max_length=500)

    @field_validator("content", "title")
    @classmethod
    def sanitize_knowledge(cls, v: str) -> str:
        return _sanitize(v)


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)
    top_k: int = Field(default=5, ge=1, le=50)

    @field_validator("query")
    @classmethod
    def sanitize_query(cls, v: str) -> str:
        return _sanitize(v)


class BulkKnowledgeRequest(BaseModel):
    brand_id: str = Field(default="bulk", max_length=64)
    items: list[dict] = Field(..., min_length=1, max_length=50)


# ═══════════════════════════════════════════════════════════════════════════
# WORKFLOW + MISC
# ═══════════════════════════════════════════════════════════════════════════


class WorkflowRunRequest(BaseModel):
    workflow: str = Field(default="post_publish", max_length=128)


class UnblockRequest(BaseModel):
    identifier: str = Field(..., min_length=1, max_length=256)

    @field_validator("identifier")
    @classmethod
    def sanitize_identifier(cls, v: str) -> str:
        return _sanitize(v)


class TenantSwitchRequest(BaseModel):
    tenant_id: str = Field(..., min_length=1, max_length=128)


class PluginExecuteRequest(BaseModel):
    command: str = Field(..., min_length=1, max_length=256)
    params: dict = Field(default_factory=dict)
    workspace_id: str = Field(default="default", max_length=64)

    @field_validator("command", "workspace_id")
    @classmethod
    def sanitize_plugin_fields(cls, v: str) -> str:
        return _sanitize(v)
