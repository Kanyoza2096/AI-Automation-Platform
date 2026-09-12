from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional
from uuid import uuid4


class EventType(str, Enum):
    """Canonical event type registry."""
    
    # Messaging
    MESSAGE_RECEIVED = "message.received"
    MESSAGE_SENT = "message.sent"
    
    # Content
    POST_GENERATED = "post.generated"
    IMAGE_RENDERED = "image.rendered"
    POST_PUBLISHED = "post.published"
    
    # Memory
    USER_REMEMBERED = "user.remembered"
    
    # Scanning
    SCAN_COMPLETED = "scan.completed"
    
    # Providers
    PROVIDER_FAILED = "provider.failed"
    
    # System
    RATE_LIMITED = "rate.limited"
    WORKER_ERROR = "worker.error"


@dataclass
class EventMetadata:
    """
    Metadata for event tracing and correlation.
    
    This class is REQUIRED by EventBus v4.1 for:
    - Correlation tracking across event chains
    - Distributed tracing (OpenTelemetry)
    - Event replay and sourcing
    - Sequence numbering for ordering
    """
    event_id: str = field(
        default_factory=lambda: f"evt_{uuid4().hex[:12]}"
    )
    correlation_id: Optional[str] = None
    parent_event_id: Optional[str] = None
    sequence_number: int = 0
    published_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    source_engine: str = "unknown"
    replay: bool = False
    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for persistence."""
        return {
            "event_id": self.event_id,
            "correlation_id": self.correlation_id,
            "parent_event_id": self.parent_event_id,
            "sequence_number": self.sequence_number,
            "published_at": self.published_at.isoformat(),
            "source_engine": self.source_engine,
            "replay": self.replay,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
        }


@dataclass
class DomainEvent:
    """
    Normalized domain event with metadata support.
    
    Compatible with EventBus v4.1 which expects:
    - event_type: EventType or str
    - payload: dict
    - source: str
    - correlation_id: Optional[str]
    - parent_event_id: Optional[str]
    - metadata: Optional[EventMetadata]
    - occurred_at: datetime
    - workspace_id: preferred on payload / nerve for tenant events
    """
    event_type: EventType
    payload: dict[str, Any] = field(default_factory=dict)
    workspace_id: Optional[str] = None
    event_id: str = field(default_factory=lambda: str(uuid4()))
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = "platform"
    correlation_id: Optional[str] = None
    parent_event_id: Optional[str] = None
    metadata: Optional[EventMetadata] = None

    def __post_init__(self) -> None:
        """Validate event after initialization."""
        # Allow string event types (convert to EventType if possible)
        if isinstance(self.event_type, str):
            try:
                self.event_type = EventType(self.event_type)
            except ValueError:
                # Keep as string for custom event types
                pass
        
        # Ensure payload is always a dict
        if not isinstance(self.payload, dict):
            self.payload = {"data": self.payload}
        
        # Create metadata if not provided
        if self.metadata is None:
            self.metadata = EventMetadata(
                event_id=self.event_id,
                source_engine=self.source,
                correlation_id=self.correlation_id,
                parent_event_id=self.parent_event_id,
            )

    @property
    def event_id_from_metadata(self) -> str:
        """Get event ID from metadata (fallback to direct attribute)."""
        return self.metadata.event_id if self.metadata else self.event_id

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses."""
        event_type_str = (
            self.event_type.value
            if isinstance(self.event_type, EventType)
            else str(self.event_type)
        )
        
        return {
            "event_id": self.event_id,
            "event_type": event_type_str,
            "payload": self.payload,
            "source": self.source,
            "correlation_id": self.correlation_id,
            "occurred_at": self.occurred_at.isoformat(),
            "metadata": self.metadata.to_dict() if self.metadata else {},
        }

    def __repr__(self) -> str:
        return f"<{self.event_type.value if hasattr(self.event_type, 'value') else self.event_type} id={self.event_id[:8]}>"


# ═══════════════════════════════════════════════════════════════════════════
# CONCRETE EVENT CLASSES (All Preserved)
# ═══════════════════════════════════════════════════════════════════════════

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


__all__ = [
    "EventType",
    "EventMetadata",
    "DomainEvent",
    "MessageReceivedEvent",
    "PostPublishedEvent",
    "PostGeneratedEvent",
    "ImageRenderedEvent",
    "UserRememberedEvent",
    "ScanCompletedEvent",
    "ProviderFailedEvent",
]
