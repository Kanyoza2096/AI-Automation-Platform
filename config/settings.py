"""
Kanyoza Systems AI Platform v15.0 — Strongly-typed configuration.

Architecture Principles:
  1. Startup validation catches misconfiguration before it causes runtime errors
  2. Service role key is validated separately from anon key — they serve different purposes
  3. Per-brand credentials are authoritative in multi-tenant mode
  4. All external calls have explicit timeout/retry configuration
  5. Production requires Supabase persistence (Architecture §14)
  6. Production requires valid Fernet encryption — never silently downgrade to plaintext
  7. Observability is configurable but enabled by default

v15.0 CHANGES:
  - Added enable_tracing for OpenTelemetry (default: True)
  - Added tracing_endpoint for OTLP exporter
  - Added enable_metrics for Prometheus (default: True)
  - Added correlation_id_enabled for request tracing
  - Enhanced to_safe_dict with new observability fields
  - Added enable_health_checks for health endpoint control
  - Added python_service_token for KCPG Gateway service auth

v14.6 — SERVICE_MODE (with split engines + chaos):
  Valid values:
    - "full"      → All services (default, single instance)
    - "api"       → FastAPI routes, Industry Plugins, Health (frontend-facing)
    - "core"      → Director + MetaEngine + ConditionEngine (backwards compat)
    - "director"  → Director only (autonomous CEO)
    - "meta"      → MetaEngine only (overseer)
    - "condition" → ConditionEngine only (brand intelligence)
    - "workers"   → BrandScheduler (background publishing)
    - "monitor"   → Monitor, Backup, TrendIntelligence
    - "github"    → GitHub Connector
    - "chaos"     → Chaos engineering mode (full + fault injection)
"""

from __future__ import annotations

import os
import logging
from typing import ClassVar, List
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from config.environments import Environment
from platform_version import PLATFORM_VERSION

logger = logging.getLogger("kanyoza.config")


class Settings(BaseSettings):
    """Strongly-typed application configuration."""

    platform_version: ClassVar[str] = PLATFORM_VERSION
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        validate_default=True,
    )

    # ═══════════════════════════════════════════════════════════════════
    # AI PROVIDER CONFIGURATION
    # ═══════════════════════════════════════════════════════════════════
    ai_provider: str = Field(default="gemini")
    gemini_key: str = Field(default="", description="Google Gemini API key — REQUIRED")
    gemini_model: str = Field(default="gemini-2.5-flash")
    gemini_chat_temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    gemini_post_temperature: float = Field(default=0.65, ge=0.0, le=2.0)
    gemini_safety_level: str = Field(default="medium")
    openai_api_key: str = Field(default="")
    anthropic_api_key: str = Field(default="")
    hf_token: str = Field(default="")
    cloudflare_account_id: str = Field(default="", description="Cloudflare Account ID for Workers AI")
    cloudflare_api_token: str = Field(default="", description="Cloudflare API Token for Workers AI")

    # ═══════════════════════════════════════════════════════════════════
    # AI TIMEOUTS & RETRY
    # ═══════════════════════════════════════════════════════════════════
    ai_request_timeout_seconds: int = Field(default=60, ge=10, le=300)
    ai_max_retries: int = Field(default=3, ge=1, le=5)
    ai_retry_base_delay_seconds: float = Field(default=2.0, ge=0.5, le=30.0)
    ai_max_tokens_per_job: int = Field(default=4096, ge=256, le=32768)

    # ═══════════════════════════════════════════════════════════════════
    # CORE PLATFORM
    # ═══════════════════════════════════════════════════════════════════
    default_workspace_id: str = Field(default="default")
    encryption_key: str = Field(
        default="",
        description="KDO-prefixed Fernet encryption key for credential vault — REQUIRED in production. "
                    "Generate with CredentialVault.generate_key() (KDO_ENCRYPTION_ prefix).",
    )
    brand_name: str = Field(default="KAAP")

    # ═══════════════════════════════════════════════════════════════════
    # REDIS
    # ═══════════════════════════════════════════════════════════════════
    upstash_redis_rest_url: str = Field(default="")
    upstash_redis_rest_token: str = Field(default="")

    # ═══════════════════════════════════════════════════════════════════
    # FACEBOOK — Legacy global fallbacks
    # ═══════════════════════════════════════════════════════════════════
    page_access_token: str = Field(default="")
    page_id: str = Field(default="")
    verify_token: str = Field(default="")
    app_secret: str = Field(default="")
    owner_psid: str = Field(default="")

    # ═══════════════════════════════════════════════════════════════════
    # EXTERNAL API TIMEOUTS
    # ═══════════════════════════════════════════════════════════════════
    facebook_connect_timeout_seconds: int = Field(default=10, ge=1, le=30)
    facebook_read_timeout_seconds: int = Field(default=30, ge=5, le=120)
    facebook_max_retries: int = Field(default=3, ge=1, le=5)
    facebook_retry_base_delay_seconds: float = Field(default=2.0, ge=0.5, le=30.0)
    external_api_connect_timeout_seconds: int = Field(default=10, ge=1, le=30)
    external_api_read_timeout_seconds: int = Field(default=30, ge=5, le=120)

    # ═══════════════════════════════════════════════════════════════════
    # PUBLISHING
    # ═══════════════════════════════════════════════════════════════════
    allow_text_only_fallback: bool = Field(default=True)
    publish_max_retries: int = Field(default=3, ge=1, le=5)
    publish_retry_base_delay_seconds: float = Field(default=2.0, ge=0.5, le=60.0)

    # ═══════════════════════════════════════════════════════════════════
    # RENDERING
    # ═══════════════════════════════════════════════════════════════════
    render_max_retries: int = Field(default=3, ge=1, le=5)
    render_timeout_seconds: int = Field(default=30, ge=5, le=120)
    max_image_width: int = Field(default=1920, ge=100, le=4096)
    max_image_height: int = Field(default=1920, ge=100, le=4096)
    max_image_size_bytes: int = Field(default=10 * 1024 * 1024, ge=100_000, le=50 * 1024 * 1024)

    # ═══════════════════════════════════════════════════════════════════
    # JOB & SCHEDULER
    # ═══════════════════════════════════════════════════════════════════
    job_max_attempts: int = Field(default=3, ge=1, le=10)
    job_stale_threshold_minutes: int = Field(default=15, ge=5, le=60)
    job_retry_backoff_multiplier: float = Field(default=2.0, ge=1.0, le=10.0)
    job_max_delay_seconds: int = Field(default=60, ge=10, le=600)
    scheduler_startup_grace_seconds: int = Field(default=30, ge=5, le=120)
    scheduler_recovery_window_minutes: int = Field(default=30, ge=1, le=60)

    # ═══════════════════════════════════════════════════════════════════
    # MESSAGING
    # ═══════════════════════════════════════════════════════════════════
    telegram_bot_token: str = Field(default="")
    telegram_chat_id: str = Field(default="")
    health_alert_chat_id: str = Field(default="")
    daily_summary_time_utc: str = Field(default="08:00")
    whatsapp_token: str = Field(default="")
    whatsapp_phone_id: str = Field(default="")

    # ═══════════════════════════════════════════════════════════════════
    # TWITTER / X
    # ═══════════════════════════════════════════════════════════════════
    twitter_api_key: str = Field(default="")
    twitter_api_secret: str = Field(default="")
    twitter_access_token: str = Field(default="")
    twitter_refresh_token: str = Field(default="")

    # ═══════════════════════════════════════════════════════════════════
    # LINKEDIN
    # ═══════════════════════════════════════════════════════════════════
    linkedin_access_token: str = Field(default="")
    linkedin_person_urn: str = Field(default="")

    # ═══════════════════════════════════════════════════════════════════
    # SUPABASE
    # ═══════════════════════════════════════════════════════════════════
    supabase_url: str = Field(default="")
    supabase_anon_key: str = Field(default="", description="Frontend reads (respects RLS)")
    supabase_service_role_key: str = Field(
        default="",
        description="Backend writes (bypasses RLS). REQUIRED in production. "
                    "Get from: Supabase Dashboard → Settings → API → service_role secret",
    )
    supabase_connect_timeout_seconds: int = Field(default=10, ge=1, le=30)
    supabase_query_timeout_seconds: int = Field(default=30, ge=5, le=120)
    supabase_max_retries: int = Field(default=3, ge=1, le=5)

    # ═══════════════════════════════════════════════════════════════════
    # RATE LIMITING
    # ═══════════════════════════════════════════════════════════════════
    rate_limit_per_user: int = Field(default=10, ge=1)
    rate_window_seconds: int = Field(default=60, ge=1)
    api_rate_limit_publish_per_brand: int = Field(default=5, ge=1, le=50)
    api_rate_limit_ai_generate_per_user: int = Field(default=20, ge=1, le=100)
    api_rate_limit_ai_chat_per_user: int = Field(default=20, ge=1, le=100)
    api_rate_limit_key_generation_per_user: int = Field(default=10, ge=1, le=100)

    # ═══════════════════════════════════════════════════════════════════
    # MEMORY
    # ═══════════════════════════════════════════════════════════════════
    max_conversation_history: int = Field(default=30, ge=1)
    max_memory_items: int = Field(default=50, ge=1)
    memory_ttl_seconds: int = Field(default=3600, ge=60)

    # ═══════════════════════════════════════════════════════════════════
    # POSTING SCHEDULE
    # ═══════════════════════════════════════════════════════════════════
    content_posting_hours_utc: List[int] = Field(default=[1, 3, 5, 7, 9, 11, 13, 15, 17, 19])
    news_posting_hours_utc: List[int] = Field(default=[6, 14, 20])

    # ═══════════════════════════════════════════════════════════════════
    # GITHUB / CODE GUARDIAN
    # ═══════════════════════════════════════════════════════════════════
    github_username: str = Field(default="")
    github_token: str = Field(default="")
    guardian_enabled: bool = Field(default=False)
    guardian_scan_hours: int = Field(default=24, ge=1)
    guardian_exclude_repos: List[str] = Field(default_factory=list)
    guardian_auto_fix: bool = Field(default=False)
    github_access_token: str = Field(default="", description="GitHub personal access token for GitHub Connector Plugin")

    # ═══════════════════════════════════════════════════════════════════
    # API ACCESS
    # ═══════════════════════════════════════════════════════════════════
    master_api_token: str = Field(default="")
    python_service_token: str = Field(
        default="",
        description="Dedicated KCPG Gateway service credential. Species-level access.",
    )
    platform_super_admin_user_ids: str = Field(
        default="",
        description="Comma-separated Supabase Auth user UUIDs explicitly granted platform.super_admin. Never populate from user metadata.",
    )
    platform_operator_user_ids: str = Field(
        default="",
        description="Comma-separated Supabase Auth user UUIDs explicitly granted platform.operator.",
    )
    kcpg_internal_events_url: str = Field(default="", description="KCPG internal event ingress URL, e.g. https://gateway/api/v1/internal/events")
    kcpg_event_signing_secret: str = Field(default="", description="Shared HMAC secret for KCPG event ingress")
    admin_hash_pepper: str = Field(
        default="",
        description="Pepper for admin password hashing. Used by GrowthBridge admin auth."
    )

    # ═══════════════════════════════════════════════════════════════════
    # CHAOS ENGINEERING
    # ═══════════════════════════════════════════════════════════════════
    chaos_enabled: bool = Field(
        default=False,
        description="Enable chaos engineering endpoints for fault injection testing",
    )
    chaos_fault_injection_rate: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Probability of fault injection (0.0-1.0) when chaos mode is active",
    )
    chaos_max_downtime_seconds: int = Field(
        default=30,
        ge=5,
        le=300,
        description="Maximum simulated downtime duration in seconds",
    )

    # ═══════════════════════════════════════════════════════════════════
    # OBSERVABILITY (v15.0 — NEW)
    # ═══════════════════════════════════════════════════════════════════
    enable_tracing: bool = Field(
        default=True,
        description="Enable OpenTelemetry distributed tracing. Set to false to disable.",
    )
    tracing_endpoint: str = Field(
        default="",
        description="OTLP endpoint for trace export (e.g., http://jaeger:4317)",
    )
    enable_metrics: bool = Field(
        default=True,
        description="Enable Prometheus metrics collection.",
    )
    metrics_endpoint: str = Field(
        default="/metrics",
        description="Path for Prometheus metrics endpoint.",
    )
    enable_health_checks: bool = Field(
        default=True,
        description="Enable health check endpoints (/health, /health/live, /health/ready).",
    )
    correlation_id_enabled: bool = Field(
        default=True,
        description="Enable correlation ID propagation for request tracing.",
    )
    tracing_sample_rate: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Sampling rate for tracing (0.0 = none, 1.0 = all requests).",
    )

    # ═══════════════════════════════════════════════════════════════════
    # CORS
    # ═══════════════════════════════════════════════════════════════════
    mutex_url: str = Field(default="")

    @property
    def cors_allowed_origins(self) -> List[str]:
        origins: set[str] = set()
        raw = os.environ.get("ALLOWED_ORIGINS", "")
        if raw:
            for o in raw.split(","):
                o = o.strip()
                if o:
                    origins.add(o)
        if self.mutex_url:
            origins.add(self.mutex_url.rstrip("/"))
        return sorted(origins)

    # ═══════════════════════════════════════════════════════════════════
    # SERVER
    # ═══════════════════════════════════════════════════════════════════
    port: int = Field(
        default_factory=lambda: int(os.environ.get("PORT", "10000") or "5000"),
        ge=1,
        le=65535,
        description="HTTP listen port — MUST follow Render $PORT in production",
    )
    base_path: str = Field(default="/bot")
    environment: str = Field(default="production")
    log_level: str = Field(default="INFO")

    # v14.6: Service mode for distributed deployment
    service_mode: str = Field(
        default="full",
        description=(
            "Which services to start. Valid: 'full', 'api', 'core', "
            "'director', 'meta', 'condition', 'workers', 'monitor', "
            "'github', 'chaos'"
        )
    )

    # ═══════════════════════════════════════════════════════════════════
    # VALIDATORS
    # ═══════════════════════════════════════════════════════════════════

    @field_validator("content_posting_hours_utc", "news_posting_hours_utc", mode="before")
    @classmethod
    def parse_hour_list(cls, v):
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

    @field_validator("service_mode")
    @classmethod
    def validate_service_mode(cls, v: str) -> str:
        valid = {
            "full", "api", "core",
            "director", "meta", "condition",
            "workers", "monitor", "github",
            "chaos",
        }
        lower = v.lower()
        if lower not in valid:
            raise ValueError(f"service_mode must be one of {valid}")
        return lower

    @model_validator(mode="after")
    def validate_supabase_key_separation(self):
        if self.supabase_anon_key and self.supabase_service_role_key:
            if self.supabase_anon_key == self.supabase_service_role_key:
                logger.warning(
                    "SUPABASE_ANON_KEY and SUPABASE_SERVICE_ROLE_KEY are identical. "
                    "They should be different keys with different permissions."
                )
        return self

    @model_validator(mode="after")
    def validate_chaos_mode(self):
        """Phase F: chaos only outside production; enable flags when allowed."""
        env = (self.environment or "production").lower()
        if self.service_mode == "chaos":
            if env in ("production", "prod"):
                logger.error(
                    "SERVICE_MODE=chaos refused in production — "
                    "falling back to service_mode=full and chaos_enabled=False"
                )
                object.__setattr__(self, "service_mode", "full")
                object.__setattr__(self, "chaos_enabled", False)
            else:
                object.__setattr__(self, "chaos_enabled", True)
                logger.info(
                    "Chaos mode activated (env=%s, rate: %.0f%%, max downtime: %ds)",
                    env,
                    self.chaos_fault_injection_rate * 100,
                    self.chaos_max_downtime_seconds,
                )
        elif self.chaos_enabled and env in ("production", "prod"):
            logger.error("chaos_enabled=True refused in production — forcing False")
            object.__setattr__(self, "chaos_enabled", False)
        return self

    # ═══════════════════════════════════════════════════════════════════
    # DERIVED PROPERTIES
    # ═══════════════════════════════════════════════════════════════════

    @property
    def env(self) -> Environment:
        return Environment.from_string(self.environment)

    @property
    def is_production(self) -> bool:
        return self.env.is_production

    @property
    def is_development(self) -> bool:
        return self.env.is_development

    @property
    def is_configured(self) -> bool:
        return bool(self.gemini_key)

    @property
    def facebook_configured(self) -> bool:
        return bool(self.page_access_token and self.page_id)

    @property
    def supabase_enabled(self) -> bool:
        return bool(self.supabase_url and self.supabase_anon_key)

    @property
    def supabase_admin_enabled(self) -> bool:
        return bool(self.supabase_url and self.supabase_service_role_key)

    @property
    def guardian_configured(self) -> bool:
        return bool(self.github_token and self.github_username and self.guardian_enabled)

    @property
    def redis_configured(self) -> bool:
        return bool(self.upstash_redis_rest_url and self.upstash_redis_rest_token)

    @property
    def is_split_engine_mode(self) -> bool:
        """True when running a single engine split across instances."""
        return self.service_mode in ("director", "meta", "condition")

    @property
    def is_intelligence_mode(self) -> bool:
        """True when running any intelligence engine."""
        return self.service_mode in ("full", "core", "director", "meta", "condition", "chaos")

    @property
    def is_api_mode(self) -> bool:
        """True when running API-facing services."""
        return self.service_mode in ("full", "api", "chaos")

    @property
    def is_background_mode(self) -> bool:
        """True when running background workers."""
        return self.service_mode in ("workers", "monitor", "github")

    @property
    def is_chaos_mode(self) -> bool:
        """True when running chaos engineering mode."""
        return self.service_mode == "chaos"

    @property
    def observability_enabled(self) -> bool:
        """True when any observability feature is enabled."""
        return self.enable_tracing or self.enable_metrics or self.enable_health_checks

    # ═══════════════════════════════════════════════════════════════════
    # RETRY CONFIG HELPERS
    # ═══════════════════════════════════════════════════════════════════

    def get_ai_retry_config(self) -> dict:
        return {
            "max_attempts": self.ai_max_retries,
            "base_delay_seconds": self.ai_retry_base_delay_seconds,
            "max_delay_seconds": self.job_max_delay_seconds,
            "backoff_multiplier": self.job_retry_backoff_multiplier,
        }

    def get_publish_retry_config(self) -> dict:
        return {
            "max_attempts": self.publish_max_retries,
            "base_delay_seconds": self.publish_retry_base_delay_seconds,
            "max_delay_seconds": self.job_max_delay_seconds,
            "backoff_multiplier": self.job_retry_backoff_multiplier,
        }

    def get_render_retry_config(self) -> dict:
        return {
            "max_attempts": self.render_max_retries,
            "base_delay_seconds": 1.0,
            "max_delay_seconds": self.job_max_delay_seconds,
            "backoff_multiplier": self.job_retry_backoff_multiplier,
        }

    def get_supabase_retry_config(self) -> dict:
        return {
            "max_attempts": self.supabase_max_retries,
            "base_delay_seconds": 1.0,
            "max_delay_seconds": 10.0,
            "backoff_multiplier": 2.0,
        }

    def get_observability_config(self) -> dict:
        """Get observability configuration."""
        return {
            "enable_tracing": self.enable_tracing,
            "tracing_endpoint": self.tracing_endpoint,
            "tracing_sample_rate": self.tracing_sample_rate,
            "enable_metrics": self.enable_metrics,
            "metrics_endpoint": self.metrics_endpoint,
            "enable_health_checks": self.enable_health_checks,
            "correlation_id_enabled": self.correlation_id_enabled,
        }

    # ═══════════════════════════════════════════════════════════════════
    # STARTUP VALIDATION
    # ═══════════════════════════════════════════════════════════════════

    REQUIRED_VARS: ClassVar[tuple] = ("gemini_key",)
    WARN_VARS: ClassVar[tuple] = ("supabase_url", "supabase_service_role_key")

    def missing_required(self) -> List[str]:
        env_names = {"gemini_key": "GEMINI_KEY"}
        return [env_names[f] for f in self.REQUIRED_VARS if not getattr(self, f)]

    def missing_warn(self) -> List[str]:
        env_names = {
            "supabase_url": "SUPABASE_URL",
            "supabase_service_role_key": "SUPABASE_SERVICE_ROLE_KEY",
        }
        return [env_names[f] for f in self.WARN_VARS if not getattr(self, f)]

    def validate_required(self) -> None:
        """Validate mandatory configuration at startup."""
        env = Environment.from_string(self.environment)
        errors: List[str] = []

        missing = self.missing_required()
        if missing:
            if env.is_production:
                logger.critical(
                    "Missing required environment variables: %s — exiting.",
                    ", ".join(missing),
                )
                import sys
                sys.exit(1)
            else:
                logger.warning(
                    "Missing environment variables: %s — running in DEGRADED mode.",
                    ", ".join(missing),
                )

        if env.is_production:
            if not self.supabase_url:
                errors.append("SUPABASE_URL is required in production.")
            if not self.supabase_service_role_key:
                errors.append(
                    "SUPABASE_SERVICE_ROLE_KEY is required in production. "
                    "Get it from: Supabase Dashboard → Settings → API → service_role secret"
                )
            if not self.supabase_anon_key:
                errors.append(
                    "SUPABASE_ANON_KEY is required in production."
                )

        if env.is_production:
            if not self.encryption_key:
                errors.append(
                    "ENCRYPTION_KEY is required in production. "
                    "Generate with: python -c \"import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())\""
                )
            else:
                try:
                    from cryptography.fernet import Fernet
                    Fernet(
                        self.encryption_key.encode()
                        if isinstance(self.encryption_key, str)
                        else self.encryption_key
                    )
                except ImportError:
                    errors.append("cryptography package is not installed.")
                except Exception:
                    errors.append(
                        "ENCRYPTION_KEY is not a valid Fernet key. "
                        "Generate with: python -c \"import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())\""
                    )

        if errors:
            for err in errors:
                logger.critical("Configuration error: %s", err)
            raise ValueError(
                "Production configuration incomplete:\n\n"
                + "\n".join(f"  • {e}" for e in errors)
                + "\n\nSet these environment variables and restart."
            )

        warn_missing = self.missing_warn()
        if warn_missing and not env.is_production:
            logger.warning(
                "Missing optional environment variables: %s — some features disabled.",
                ", ".join(warn_missing),
            )

        if self.supabase_anon_key and not self.supabase_service_role_key:
            logger.warning(
                "SUPABASE_ANON_KEY is set but SUPABASE_SERVICE_ROLE_KEY is not. "
                "Backend writes will fail with 403 errors on RLS-protected tables."
            )

    def to_safe_dict(self) -> dict:
        """Return safe configuration summary (no secrets)."""
        return {
            "platform_version": self.platform_version,
            "ai_provider": self.ai_provider,
            "gemini_model": self.gemini_model,
            "gemini_key_configured": bool(self.gemini_key),
            "facebook_configured": self.facebook_configured,
            "supabase_enabled": self.supabase_enabled,
            "supabase_admin_enabled": self.supabase_admin_enabled,
            "guardian_enabled": self.guardian_configured,
            "twitter_configured": bool(self.twitter_access_token),
            "linkedin_configured": bool(self.linkedin_access_token),
            "redis_configured": self.redis_configured,
            "environment": self.environment,
            "log_level": self.log_level,
            "service_mode": self.service_mode,
            "is_split_engine_mode": self.is_split_engine_mode,
            "is_intelligence_mode": self.is_intelligence_mode,
            "is_chaos_mode": self.is_chaos_mode,
            "chaos_enabled": self.chaos_enabled,
            "rate_limit_per_user": self.rate_limit_per_user,
            "content_posting_hours_utc": self.content_posting_hours_utc,
            "news_posting_hours_utc": self.news_posting_hours_utc,
            "allow_text_only_fallback": self.allow_text_only_fallback,
            "job_max_attempts": self.job_max_attempts,
            "job_stale_threshold_minutes": self.job_stale_threshold_minutes,
            # v15.0 — Observability
            "enable_tracing": self.enable_tracing,
            "tracing_endpoint": self.tracing_endpoint,
            "enable_metrics": self.enable_metrics,
            "enable_health_checks": self.enable_health_checks,
            "correlation_id_enabled": self.correlation_id_enabled,
            "observability_enabled": self.observability_enabled,
            # KCPG Gateway
            "python_service_token_configured": bool(self.python_service_token),
        }


settings = Settings()


def get_settings() -> Settings:
    """Get the singleton settings instance."""
    return settings


__all__ = [
    "Settings",
    "settings",
    "get_settings",
]
