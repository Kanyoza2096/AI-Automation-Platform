from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from workflows.base import Step, Workflow

if TYPE_CHECKING:
    from services.content_engine import ContentEngine
    from plugins.base import PluginRegistry
    from core.event_bus import EventBus
    from repositories.analytics_repository import AnalyticsRepository

logger = logging.getLogger(__name__)


class SelectTopicStep(Step):
    """Pick a topic from the domain selector."""

    name = "select_topic"

    def __init__(self, category: str | None = None) -> None:
        self._category = category

    def run(self, context: dict[str, Any]) -> Any:
        from domain.content.topic_selector import TopicSelector
        selector = TopicSelector()
        topic, cat = selector.pick(self._category)
        context["topic"] = topic
        context["category"] = cat
        logger.info("PostWorkflow: topic='%s' category='%s'", topic, cat)
        return {"topic": topic, "category": cat}


class GenerateContentStep(Step):
    """Call ContentEngine to produce a caption + PNG image bytes."""

    name = "generate_content"

    def __init__(self, content_engine: "ContentEngine") -> None:
        self._engine = content_engine

    def run(self, context: dict[str, Any]) -> Any:
        topic = context.get("topic", "")
        result = self._engine.generate_post(topic=topic)
        if not result:
            raise RuntimeError(f"ContentEngine returned no result for '{topic}'")

        # Normalise to dict — generate_post() always returns a dict in v10
        if isinstance(result, tuple):
            # Backward-compat shim: (caption, image_bytes) tuple from older code
            caption, image_bytes = result[0], result[1] if len(result) > 1 else None
            result = {"caption": caption, "image_bytes": image_bytes, "topic": topic, "category": ""}

        context["content_result"] = result
        # Promote key fields to top-level context for downstream steps
        context["caption"] = result.get("caption", "")
        context["image_bytes"] = result.get("image_bytes")
        return result


class RenderImageStep(Step):
    """Pass image bytes from ContentEngine through to PublishStep.

    ContentEngine already renders the card PNG internally, so this step is
    lightweight — it just promotes image_bytes into context and logs the size.
    continue_on_failure = True so a render glitch never blocks publishing.
    """

    name = "render_image"
    continue_on_failure = True

    def __init__(self, content_engine: "ContentEngine") -> None:
        self._engine = content_engine

    def run(self, context: dict[str, Any]) -> Any:
        image_bytes = context.get("image_bytes")
        if image_bytes:
            logger.info(
                "RenderImageStep: PNG ready (%d bytes) — passing to publisher",
                len(image_bytes),
            )
        else:
            logger.debug("RenderImageStep: no image bytes — text-only post")
        context["image_bytes"] = image_bytes
        return image_bytes


class PublishStep(Step):
    """Publish via the plugin registry (Facebook + any other active plugins)."""

    name = "publish"

    def __init__(self, registry: "PluginRegistry") -> None:
        self._registry = registry

    def run(self, context: dict[str, Any]) -> Any:
        caption = context.get("caption") or context.get("content_result", {}).get("caption", "")
        image_bytes = context.get("image_bytes")

        if not caption:
            raise RuntimeError("No caption available — cannot publish")

        results = self._registry.publish_all(caption, image_bytes=image_bytes)
        context["publish_results"] = results
        logger.info("PostWorkflow: published to %s", list(results.keys()))
        return results


class RecordAnalyticsStep(Step):
    """Persist analytics event after publishing."""

    name = "record_analytics"
    continue_on_failure = True

    def __init__(self, analytics_repo: "AnalyticsRepository") -> None:
        self._repo = analytics_repo

    def run(self, context: dict[str, Any]) -> Any:
        topic = context.get("topic", "")
        publish_results = context.get("publish_results", {})
        for platform, result in publish_results.items():
            if result.get("ok"):
                self._repo.record_post_published(
                    topic=topic,
                    platform=platform,
                    post_id=result.get("post_id", ""),
                )
        return {"recorded": len(publish_results)}


class EmitEventStep(Step):
    """Emit a PostPublished domain event to the bus."""

    name = "emit_event"
    continue_on_failure = True

    def __init__(self, bus: "EventBus") -> None:
        self._bus = bus

    def run(self, context: dict[str, Any]) -> Any:
        from core.events import PostPublishedEvent
        topic = context.get("topic", "")
        publish_results = context.get("publish_results", {})
        content_result = context.get("content_result", {})
        caption = content_result.get("caption", "")

        for platform, result in publish_results.items():
            if result.get("ok"):
                self._bus.publish(
                    PostPublishedEvent(
                        post_id=result.get("post_id", ""),
                        topic=topic,
                        platform=platform,
                        caption_preview=caption,
                    )
                )
        return None


class PostPublishWorkflow(Workflow):
    """Full post pipeline: Select → Generate → Render → Publish → Analytics → Event.

    Instantiate once and call run() for each scheduled or manual post.
    """

    name = "post_publish"

    def __init__(
        self,
        content_engine: "ContentEngine",
        registry: "PluginRegistry",
        analytics_repo: "AnalyticsRepository",
        bus: "EventBus",
        category: str | None = None,
    ) -> None:
        super().__init__(
            steps=[
                SelectTopicStep(category),
                GenerateContentStep(content_engine),
                RenderImageStep(content_engine),
                PublishStep(registry),
                RecordAnalyticsStep(analytics_repo),
                EmitEventStep(bus),
            ]
        )
