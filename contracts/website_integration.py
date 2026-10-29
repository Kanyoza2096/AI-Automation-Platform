"""Canonical contract for Next.js applications integrating with KDO Website Connector."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from pydantic import BaseModel, ConfigDict, Field, field_validator


class WebsiteCapability(StrEnum):
    READ_ARTICLES = "READ_ARTICLES"
    CREATE_DRAFT = "CREATE_DRAFT"
    UPDATE_ARTICLE = "UPDATE_ARTICLE"
    PUBLISH_ARTICLE = "PUBLISH_ARTICLE"
    DELETE_ARTICLE = "DELETE_ARTICLE"
    READ_ANALYTICS = "READ_ANALYTICS"


class WebsiteEventType(StrEnum):
    ARTICLE_CREATED = "article.created"
    ARTICLE_UPDATED = "article.updated"
    ARTICLE_PUBLISHED = "article.published"
    ARTICLE_DELETED = "article.deleted"
    CONTACT_SUBMITTED = "contact.submitted"
    COMMENT_CREATED = "comment.created"


class WebsiteIntegrationStatus(StrEnum):
    REGISTERED = "registered"
    CONFIGURED = "configured"
    CONNECTED = "connected"
    DEGRADED = "degraded"
    DISCONNECTED = "disconnected"
    REVOKED = "revoked"


class WebsiteIntegrationRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    integration_id: str = Field(min_length=3, max_length=128, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
    app_name: str = Field(min_length=1, max_length=160)
    environment: str = Field(default="production", min_length=1, max_length=32)
    base_url: str = Field(min_length=1, max_length=2048)
    publish_path: str = Field(default="/api/kdo/content", min_length=1, max_length=512)
    lookup_path_template: str = Field(default="", max_length=512)
    edit_path_template: str = Field(default="", max_length=512)
    delete_path_template: str = Field(default="", max_length=512)
    event_types: list[WebsiteEventType] = Field(default_factory=list, max_length=50)
    capabilities: list[WebsiteCapability] = Field(default_factory=list, max_length=50)
    status: WebsiteIntegrationStatus = WebsiteIntegrationStatus.REGISTERED

    @field_validator("base_url")
    @classmethod
    def https_only(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.startswith("https://"):
            raise ValueError("base_url must use HTTPS")
        return value


class WebsiteEventEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    integration_id: str = Field(min_length=3, max_length=128)
    event_id: str = Field(min_length=8, max_length=256)
    event_type: WebsiteEventType
    event_version: str = Field(default="1.0", max_length=20)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    payload: dict[str, Any] = Field(default_factory=dict)


class WebsiteIntegrationReadModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    integration_id: str
    workspace_id: str
    app_name: str
    environment: str
    status: WebsiteIntegrationStatus
    capabilities: list[WebsiteCapability]
    event_types: list[WebsiteEventType]
    connector_platform: str = "website"
    connector_authority: str = "WebsiteConnector"
    side_effect_executor: str = "canonical_action_kernel"
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_event_at: datetime | None = None
    last_health_at: datetime | None = None
    last_health_ok: bool | None = None
