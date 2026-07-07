"""
Kanyoza Systems AI Platform v10 — Strongly-typed configuration.

Uses pydantic-settings so every field is:
  - Type-validated on startup
  - Loaded automatically from environment variables
  - Documented with a description

Call Settings.validate_required() at bootstrap — the process will refuse
to start with missing required variables.
"""

from __future__ import annotations

import sys
import logging
from typing import ClassVar, List, Optional
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from config.environments import Environment

logger = logging.getLogger("kanyoza.config")


class Settings(BaseSettings):
    """
    Single source of truth for all runtime configuration.
    All fields are loaded from environment variables (case-insensitive).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        validate_default=True,
    )

    # -------------------------------------------------------------------------
    # AI — Gemini (primary)
    # -------------------------------------------------------------------------
    gemini_key: str = Field(default="", description="Google Gemini API key (GEMINI_KEY)")
    gemini_model: str = Field(default="gemini-2.5-flash", description="Gemini model identifier")
    gemini_chat_temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    gemini_post_temperature: float = Field(default=0.65, ge=0.0, le=2.0)
    gemini_safety_level: str = Field(default="medium")

    # AI — Optional providers (future milestones)
    openai_api_key: str = Field(default="", description="OpenAI API key (optional)")
    anthropic_api_key: str = Field(default="", description="Anthropic API key (optional)")
    hf_token: str = Field(default="", description="Hugging Face token (optional)")

    # Active AI provider
    ai_provider: str = Field(
        default="gemini",
        description="Active AI provider: gemini | openai | anthropic | huggingface",
    )

    # -------------------------------------------------------------------------
    # Core Platform Context
    # -------------------------------------------------------------------------
    default_workspace_id: str = Field(
        default="default",
        description="Default workspace ID for backwards compatibility"
    )
    encryption_key: str = Field(
        default="",
        description="Fernet key for encrypting credentials at rest"
    )

    # -------------------------------------------------------------------------
    # Branding
    # -------------------------------------------------------------------------
    brand_name: str = Field(default="Dev Comparisons", description="Brand name shown in the card footer (BRAND_NAME)")

    # -------------------------------------------------------------------------
    # Facebook / Messenger
    # -------------------------------------------------------------------------
    page_access_token: str = Field(default="", description="Facebook Page access token")
    page_id: str = Field(default="", description="Facebook Page ID")
    verify_token: str = Field(default="", description="Facebook webhook verify token")
    app_secret: str = Field(default="", description="Facebook app secret")
    owner_psid: str = Field(default="", description="Owner's Messenger PSID for notifications")

    # -------------------------------------------------------------------------
    # WhatsApp (optional future platform)
    # -------------------------------------------------------------------------
    whatsapp_token: str = Field(default="")
    whatsapp_phone_id: str = Field(default="")

    # -------------------------------------------------------------------------
    # Twitter/X
    # -------------------------------------------------------------------------
    twitter_api_key: str = Field(default="", description="Twitter API Key (Consumer Key)")
    twitter_api_secret: str = Field(default="", description="Twitter API Secret")
    twitter_access_token: str = Field(default="", description="Twitter OAuth 2.0 Access Token")
    twitter_refresh_token: str = Field(default="", description="Twitter OAuth 2.0 Refresh Token")

    # -------------------------------------------------------------------------
    # LinkedIn (future)
    # -------------------------------------------------------------------------
    linkedin_access_token: str = Field(default="", description="LinkedIn OAuth 2.0 Access Token")
    linkedin_person_urn: str = Field(default="", description="LinkedIn Person URN")

    # -------------------------------------------------------------------------
    # Supabase
    # -------------------------------------------------------------------------
    supabase_url: str = Field(default="")
    supabase_anon_key: str = Field(default="")
    supabase_service_role_key: str = Field(default="")

    # -------------------------------------------------------------------------
    # Rate limiting
    # -------------------------------------------------------------------------
    rate_limit_per_user: int = Field(default=10, ge=1)
    rate_window_seconds: int = Field(default=60, ge=1)

    # -------------------------------------------------------------------------
    # Memory
    # -------------------------------------------------------------------------
    max_conversation_history: int = Field(default=30, ge=1)
    max_memory_items: int = Field(default=50, ge=1)
    memory_ttl_seconds: int = Field(default=3600, ge=60, description="In-memory cache TTL")

    # -------------------------------------------------------------------------
    # Posting schedule (UTC hours as comma-separated string)
    # -------------------------------------------------------------------------
    content_posting_hours_utc: List[int] = Field(
        default=[1, 3, 5, 7, 9, 11, 13, 15, 17, 19],
        description="10 UTC hours for automated posts — one post per slot, ~2 h apart",
    )
    news_posting_hours_utc: List[int] = Field(default=[6, 14, 20])

    # -------------------------------------------------------------------------
    # GitHub / Code Guardian
    # -------------------------------------------------------------------------
    github_username: str = Field(default="")
    github_token: str = Field(default="")
    guardian_enabled: bool = Field(default=False)
    guardian_scan_hours: int = Field(default=24, ge=1)
    guardian_exclude_repos: List[str] = Field(default_factory=list)
    guardian_auto_fix: bool = Field(default=False, description="Enable auto-fix PR creation for fixable issues")

    # -------------------------------------------------------------------------
    # API access
    # -------------------------------------------------------------------------
    master_api_token: str = Field(
        default="",
        description="Bearer token for frontend/dashboard API access (MASTER_API_TOKEN)",
    )

    # -------------------------------------------------------------------------
    # Server
    # -------------------------------------------------------------------------
    port: int = Field(default=5000, ge=1, le=65535)
    base_path: str = Field(default="/bot")
    environment: str = Field(default="production")
    log_level: str = Field(default="INFO")

    # -------------------------------------------------------------------------
    # Validators
    # -------------------------------------------------------------------------

    @field_validator("content_posting_hours_utc", "news_posting_hours_utc", mode="before")
    @classmethod
    def parse_hour_list(cls, v):
        """Accept both a list and a comma-separated string."""
        if isinstance(v, list):
            return [int(x) for x in v]
        if isinstance(v, str):
            return [int(x.strip()) for x in v.split(",") if x.strip()]
        return v

    @field_validator("guardian_exclude_repos", mode="before")
    @classmethod
    def parse_repo_list(cls, v):
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            return [r.strip() for r in v.split(",") if r.strip()]
        return v

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in valid:
            raise ValueError(f"log_level must be one of {valid}")
        return upper

    # -------------------------------------------------------------------------
    # Derived properties
    # -------------------------------------------------------------------------

    @property
    def env(self) -> Environment:
        return Environment.from_string(self.environment)

    @property
    def is_development(self) -> bool:
        return self.env.is_development

    @property
    def is_configured(self) -> bool:
        """True when the minimum Facebook + Gemini config is present."""
        return bool(self.gemini_key and self.page_access_token and self.page_id)

    @property
    def supabase_enabled(self) -> bool:
        return bool(self.supabase_url and self.supabase_anon_key)

    @property
    def supabase_admin_enabled(self) -> bool:
        return bool(self.supabase_url and self.supabase_service_role_key)

    @property
    def guardian_configured(self) -> bool:
        return bool(self.github_token and self.github_username and self.guardian_enabled)

    # -------------------------------------------------------------------------
    # Startup validation
    # -------------------------------------------------------------------------

    REQUIRED_VARS: ClassVar[tuple] = (
        "gemini_key",
        "page_access_token",
        "verify_token",
        "app_secret",
    )

    def missing_required(self) -> List[str]:
        """Return names of required variables that are empty."""
        env_names = {
            "gemini_key": "GEMINI_KEY",
            "page_access_token": "PAGE_ACCESS_TOKEN",
            "verify_token": "VERIFY_TOKEN",
            "app_secret": "APP_SECRET",
        }
        return [
            env_names[field]
            for field in self.REQUIRED_VARS
            if not getattr(self, field)
        ]

    def validate_required(self) -> None:
        """Validate required environment variables."""
        missing = self.missing_required()
        if not missing:
            return

        env = Environment.from_string(self.environment)
        if env.is_production:
            logger.critical(
                "Missing required environment variables: %s — refusing to start.",
                ", ".join(missing),
            )
            sys.exit(1)
        else:
            logger.warning(
                "⚠️  Missing environment variables: %s — running in DEGRADED mode "
                "(set ENVIRONMENT=production to enforce strict validation).",
                ", ".join(missing),
            )

    def to_safe_dict(self) -> dict:
        """Return a non-sensitive settings snapshot safe for status endpoints."""
        return {
            "ai_provider": self.ai_provider,
            "gemini_model": self.gemini_model,
            "gemini_key_configured": bool(self.gemini_key),
            "facebook_configured": bool(self.page_access_token and self.page_id),
            "supabase_enabled": self.supabase_enabled,
            "guardian_enabled": self.guardian_configured,
            "twitter_configured": bool(self.twitter_access_token),
            "linkedin_configured": bool(self.linkedin_access_token),
            "environment": self.environment,
            "log_level": self.log_level,
            "rate_limit_per_user": self.rate_limit_per_user,
            "content_posting_hours_utc": self.content_posting_hours_utc,
            "news_posting_hours_utc": self.news_posting_hours_utc,
        }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
settings = Settings()


def get_settings() -> Settings:
    """Return the module-level Settings singleton."""
    return settings
