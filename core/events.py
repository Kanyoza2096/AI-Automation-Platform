from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import uuid4


class EventType(str, Enum):
    MESSAGE_RECEIVED = "message.received"
    MESSAGE_SENT = "message.sent"
    POST_GENERATED = "post.generated"
    IMAGE_RENDERED = "image.rendered"
    POST_PUBLISHED = "post.published"
    USER_REMEMBERED = "user.remembered"
    SCAN_COMPLETED = "scan.completed"
    PROVIDER_FAILED = "provider.failed"
    RATE_LIMITED = "rate.limited"
    WORKER_ERROR = "worker.error"


@dataclass
class DomainEvent:
    event_type: EventType
    payload: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid4()))
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = "platform"

    def __repr__(self) -> str:
        return f"<{self.event_type.value} id={self.event_id[:8]}>"


@dataclass
class MessageReceivedEvent(DomainEvent):
    def __init__(self, sender_id: str, text: str, platform: str = "messenger"):
        super().__init__(
            event_type=EventType.MESSAGE_RECEIVED,
            payload={"sender_id": sender_id, "text": text, "platform": platform},
            source=platform,
        )


@dataclass
class PostPublishedEvent(DomainEvent):
    def __init__(self, post_id: str, topic: str, platform: str, caption_preview: str):
        super().__init__(
            event_type=EventType.POST_PUBLISHED,
            payload={
                "post_id": post_id,
                "topic": topic,
                "platform": platform,
                "caption_preview": caption_preview[:120],
            },
            source=platform,
        )


@dataclass
class PostGeneratedEvent(DomainEvent):
    def __init__(self, topic: str, theme: str, provider: str, latency_ms: float):
        super().__init__(
            event_type=EventType.POST_GENERATED,
            payload={
                "topic": topic,
                "theme": theme,
                "provider": provider,
                "latency_ms": latency_ms,
            },
        )


@dataclass
class ImageRenderedEvent(DomainEvent):
    def __init__(self, template: str, cached: bool, latency_ms: float, size_bytes: int = 0):
        super().__init__(
            event_type=EventType.IMAGE_RENDERED,
            payload={
                "template": template,
                "cached": cached,
                "latency_ms": latency_ms,
                "size_bytes": size_bytes,
            },
        )


@dataclass
class UserRememberedEvent(DomainEvent):
    def __init__(self, sender_id: str, fact: str):
        super().__init__(
            event_type=EventType.USER_REMEMBERED,
            payload={"sender_id": sender_id, "fact": fact},
        )


@dataclass
class ScanCompletedEvent(DomainEvent):
    def __init__(self, repo: str, issues_found: int, critical: int):
        super().__init__(
            event_type=EventType.SCAN_COMPLETED,
            payload={"repo": repo, "issues_found": issues_found, "critical": critical},
        )


@dataclass
class ProviderFailedEvent(DomainEvent):
    def __init__(self, provider: str, error: str, capability: str):
        super().__init__(
            event_type=EventType.PROVIDER_FAILED,
            payload={"provider": provider, "error": error, "capability": capability},
        )
