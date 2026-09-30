"""Canonical media artifact model for the Kanyoza platform.

MediaArtifact is the single normalized contract between renderers and
downstream consumers such as publishers, schedulers, dashboards and audit
logging.

Key guarantees:
- Explicit lifecycle: NO_MEDIA, RENDERING, MEDIA_READY, RENDER_FAILED.
- Never use None to represent media state.
- Support byte-backed and URL-backed media.
- Validate state invariants at construction time.
- Preserve tracing information across the pipeline.
- Serialize safely without embedding raw media bytes.
- Remain compatible with existing ready(), no_media(), failed(), rendering()
  call sites.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional
from uuid import uuid4


class MediaStatus(str, Enum):
    """Canonical lifecycle state of a media artifact."""

    NO_MEDIA = "no_media"
    RENDERING = "rendering"
    MEDIA_READY = "media_ready"
    RENDER_FAILED = "render_failed"


@dataclass
class MediaArtifact:
    """Canonical representation of generated media.

    A ready artifact may contain raw bytes, a remote URL, or both. This is
    important because RenderQueue/HCTI can complete successfully while
    returning a URL without downloading the image into memory.
    """

    artifact_id: str = field(default_factory=lambda: uuid4().hex)

    content_type: str = "image/png"
    bytes: bytes = field(default_factory=bytes, repr=False)
    media_url: Optional[str] = None

    width: int = 1080
    height: int = 1080

    source: str = "pillow"
    status: MediaStatus = MediaStatus.NO_MEDIA

    error: Optional[str] = None
    error_code: Optional[str] = None

    topic: Optional[str] = None
    brand_id: Optional[str] = None
    task_id: Optional[str] = None

    renderer_version: Optional[str] = None
    render_time_ms: Optional[float] = None

    metadata: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        """Normalize values and enforce lifecycle invariants."""

        if isinstance(self.status, str):
            try:
                self.status = MediaStatus(self.status)
            except ValueError as exc:
                raise ValueError(
                    f"Invalid media status: {self.status!r}"
                ) from exc

        if self.bytes is None:
            self.bytes = b""
        elif isinstance(self.bytes, bytearray):
            self.bytes = bytes(self.bytes)
        elif not isinstance(self.bytes, bytes):
            raise TypeError("MediaArtifact.bytes must be bytes or bytearray")

        if self.width < 0 or self.height < 0:
            raise ValueError("Media dimensions cannot be negative")

        if self.render_time_ms is not None and self.render_time_ms < 0:
            raise ValueError("render_time_ms cannot be negative")

        self.metadata = dict(self.metadata or {})

        if self.status == MediaStatus.NO_MEDIA:
            if self.bytes or self.media_url:
                raise ValueError(
                    "NO_MEDIA cannot contain media bytes or media_url"
                )
            if self.error or self.error_code:
                raise ValueError(
                    "NO_MEDIA cannot contain error information"
                )

        elif self.status == MediaStatus.RENDERING:
            if self.bytes or self.media_url:
                raise ValueError(
                    "RENDERING cannot contain completed media"
                )
            if self.error or self.error_code:
                raise ValueError(
                    "RENDERING cannot contain error information"
                )

        elif self.status == MediaStatus.MEDIA_READY:
            if not self.bytes and not self.media_url:
                raise ValueError(
                    "MEDIA_READY requires image bytes or media_url"
                )
            if self.error or self.error_code:
                raise ValueError(
                    "MEDIA_READY cannot contain error information"
                )

        elif self.status == MediaStatus.RENDER_FAILED:
            if self.bytes or self.media_url:
                raise ValueError(
                    "RENDER_FAILED cannot contain completed media"
                )
            if not self.error:
                raise ValueError(
                    "RENDER_FAILED requires an error message"
                )
            if not self.error_code:
                self.error_code = "RENDER_FAILED"

    @property
    def size_bytes(self) -> int:
        """Size of locally stored media bytes."""
        return len(self.bytes)

    @property
    def size_kb(self) -> float:
        """Size of locally stored media in KiB."""
        return self.size_bytes / 1024 if self.bytes else 0.0

    @property
    def has_bytes(self) -> bool:
        """Whether raw media bytes are available locally."""
        return bool(self.bytes)

    @property
    def has_url(self) -> bool:
        """Whether a remote media URL is available."""
        return bool(self.media_url)

    @property
    def has_media(self) -> bool:
        """Whether usable media data is present."""
        return self.has_bytes or self.has_url

    @property
    def is_valid(self) -> bool:
        """Backward-compatible validity check."""
        return (
            self.status == MediaStatus.MEDIA_READY
            and self.has_media
        )

    @property
    def is_publishable(self) -> bool:
        """Whether a publisher can safely consume this artifact."""
        return self.status == MediaStatus.MEDIA_READY and self.has_media

    @property
    def is_text_only(self) -> bool:
        """Whether this post intentionally contains no media."""
        return self.status == MediaStatus.NO_MEDIA

    @property
    def has_failed(self) -> bool:
        """Whether rendering failed."""
        return self.status == MediaStatus.RENDER_FAILED

    @property
    def is_pending(self) -> bool:
        """Whether rendering is still in progress."""
        return self.status == MediaStatus.RENDERING

    @property
    def is_url_backed(self) -> bool:
        """Whether the ready artifact relies on a URL without local bytes."""
        return (
            self.status == MediaStatus.MEDIA_READY
            and self.has_url
            and not self.has_bytes
        )

    @property
    def is_byte_backed(self) -> bool:
        """Whether the ready artifact has local bytes."""
        return (
            self.status == MediaStatus.MEDIA_READY
            and self.has_bytes
        )

    @classmethod
    def ready(
        cls,
        image_bytes: bytes = b"",
        content_type: str = "image/png",
        width: int = 1080,
        height: int = 1080,
        source: str = "pillow",
        topic: Optional[str] = None,
        brand_id: Optional[str] = None,
        *,
        media_url: Optional[str] = None,
        task_id: Optional[str] = None,
        renderer_version: Optional[str] = None,
        render_time_ms: Optional[float] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        artifact_id: Optional[str] = None,
    ) -> "MediaArtifact":
        """Create a successful media artifact.

        Either image_bytes or media_url must be supplied. Both may be
        supplied when a renderer has both representations.
        """
        if image_bytes is None:
            image_bytes = b""

        if not image_bytes and not media_url:
            raise ValueError(
                "MediaArtifact.ready requires image_bytes or media_url"
            )

        return cls(
            artifact_id=artifact_id or uuid4().hex,
            content_type=content_type,
            bytes=image_bytes,
            media_url=media_url,
            width=width,
            height=height,
            source=source,
            status=MediaStatus.MEDIA_READY,
            topic=topic,
            brand_id=brand_id,
            task_id=task_id,
            renderer_version=renderer_version,
            render_time_ms=render_time_ms,
            metadata=dict(metadata or {}),
        )

    @classmethod
    def no_media(
        cls,
        topic: Optional[str] = None,
        brand_id: Optional[str] = None,
        *,
        metadata: Optional[Mapping[str, Any]] = None,
        artifact_id: Optional[str] = None,
    ) -> "MediaArtifact":
        """Create an intentional text-only artifact."""
        return cls(
            artifact_id=artifact_id or uuid4().hex,
            status=MediaStatus.NO_MEDIA,
            topic=topic,
            brand_id=brand_id,
            metadata=dict(metadata or {}),
        )

    @classmethod
    def failed(
        cls,
        error: str,
        error_code: str = "RENDER_FAILED",
        source: str = "pillow",
        topic: Optional[str] = None,
        brand_id: Optional[str] = None,
        *,
        task_id: Optional[str] = None,
        renderer_version: Optional[str] = None,
        render_time_ms: Optional[float] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        artifact_id: Optional[str] = None,
    ) -> "MediaArtifact":
        """Create an explicit render-failure artifact."""
        return cls(
            artifact_id=artifact_id or uuid4().hex,
            source=source,
            status=MediaStatus.RENDER_FAILED,
            error=str(error),
            error_code=error_code or "RENDER_FAILED",
            topic=topic,
            brand_id=brand_id,
            task_id=task_id,
            renderer_version=renderer_version,
            render_time_ms=render_time_ms,
            metadata=dict(metadata or {}),
        )

    @classmethod
    def rendering(
        cls,
        source: str = "render_queue",
        topic: Optional[str] = None,
        brand_id: Optional[str] = None,
        *,
        task_id: Optional[str] = None,
        renderer_version: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        artifact_id: Optional[str] = None,
    ) -> "MediaArtifact":
        """Create an artifact representing an in-progress render."""
        return cls(
            artifact_id=artifact_id or uuid4().hex,
            source=source,
            status=MediaStatus.RENDERING,
            topic=topic,
            brand_id=brand_id,
            task_id=task_id,
            renderer_version=renderer_version,
            metadata=dict(metadata or {}),
        )

    def with_ready_media(
        self,
        image_bytes: bytes = b"",
        *,
        media_url: Optional[str] = None,
        content_type: Optional[str] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        source: Optional[str] = None,
        render_time_ms: Optional[float] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> "MediaArtifact":
        """Transition this artifact to MEDIA_READY while preserving identity."""
        return MediaArtifact.ready(
            image_bytes=image_bytes,
            media_url=media_url,
            content_type=content_type or self.content_type,
            width=self.width if width is None else width,
            height=self.height if height is None else height,
            source=source or self.source,
            topic=self.topic,
            brand_id=self.brand_id,
            task_id=self.task_id,
            renderer_version=self.renderer_version,
            render_time_ms=(
                self.render_time_ms
                if render_time_ms is None
                else render_time_ms
            ),
            metadata=self.metadata if metadata is None else metadata,
            artifact_id=self.artifact_id,
        )

    def with_failure(
        self,
        error: str,
        error_code: str = "RENDER_FAILED",
        *,
        source: Optional[str] = None,
        render_time_ms: Optional[float] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> "MediaArtifact":
        """Transition this artifact to RENDER_FAILED."""
        return MediaArtifact.failed(
            error=error,
            error_code=error_code,
            source=source or self.source,
            topic=self.topic,
            brand_id=self.brand_id,
            task_id=self.task_id,
            renderer_version=self.renderer_version,
            render_time_ms=(
                self.render_time_ms
                if render_time_ms is None
                else render_time_ms
            ),
            metadata=self.metadata if metadata is None else metadata,
            artifact_id=self.artifact_id,
        )

    def to_dict(self) -> dict[str, Any]:
        """Safe API/log serialization; raw bytes are never included."""
        result: dict[str, Any] = {
            "artifact_id": self.artifact_id,
            "content_type": self.content_type,
            "width": self.width,
            "height": self.height,
            "size_bytes": self.size_bytes,
            "size_kb": round(self.size_kb, 1),
            "has_bytes": self.has_bytes,
            "has_url": self.has_url,
            "media_url": self.media_url,
            "source": self.source,
            "status": self.status.value,
            "valid": self.is_valid,
            "publishable": self.is_publishable,
        }

        if self.topic:
            result["topic"] = self.topic
        if self.brand_id:
            result["brand_id"] = self.brand_id
        if self.task_id:
            result["task_id"] = self.task_id
        if self.renderer_version:
            result["renderer_version"] = self.renderer_version
        if self.render_time_ms is not None:
            result["render_time_ms"] = round(self.render_time_ms, 2)
        if self.error:
            result["error"] = self.error
        if self.error_code:
            result["error_code"] = self.error_code
        if self.metadata:
            result["metadata"] = dict(self.metadata)

        return result

    def to_publish_dict(self) -> dict[str, Any]:
        """Compact publisher-facing representation without raw bytes."""
        return {
            "artifact_id": self.artifact_id,
            "status": self.status.value,
            "content_type": self.content_type,
            "width": self.width,
            "height": self.height,
            "size_bytes": self.size_bytes,
            "size_kb": round(self.size_kb, 1),
            "media_url": self.media_url,
            "source": self.source,
            "valid": self.is_valid,
            "publishable": self.is_publishable,
        }

    def __bool__(self) -> bool:
        """True only when the artifact is ready and publishable."""
        return self.is_publishable


__all__ = ["MediaArtifact", "MediaStatus"]
