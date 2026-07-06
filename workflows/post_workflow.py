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

    def __init__(self, brand: "Brand", category: str | None = None) -> None:
        self._brand = brand
        self._category = category

    def run(self, context: dict[str, Any]) -> Any:
        from domain.content.topic_selector import TopicSelector
        selector = TopicSelector(brand=self._brand)
        topic, cat = selector.pick(self._category)
        context["topic"] = topic
        context["category"] = cat
        logger.info("PostWorkflow: topic='%s' category='%s' brand='%s'", topic, cat, self._brand.name)
        return {"topic": topic, "category": cat}


class GenerateContentStep(Step):
    """Call ContentEngine to produce a caption + PNG image bytes."""
    name = "generate_content"

    def __init__(self, content_engine: "ContentEngine", brand: "Brand", ai_profile: "AIProfile") -> None:
        self._engine = content_engine
        self._brand = brand
        self._ai_profile = ai_profile

    def run(self, context: dict[str, Any]) -> Any:
        topic = context.get("topic", "")
        result = self._engine.generate_post(brand=self._brand, ai_profile=self._ai_profile, topic=topic)
        if not result:
            raise RuntimeError(f"ContentEngine returned no result for '{topic}'")

        context["content_result"] = result
        context["caption"] = result.get("caption", "")
        context["image_bytes"] = result.get("image_bytes")
        return result


class RenderImageStep(Step):
    name = "render_image"
    continue_on_failure = True

    def __init__(self, content_engine: "ContentEngine") -> None:
        self._engine = content_engine

    def run(self, context: dict[str, Any]) -> Any:
        image_bytes = context.get("image_bytes")
        if image_bytes:
            logger.info("RenderImageStep: PNG ready (%d bytes)", len(image_bytes))
        context["image_bytes"] = image_bytes
        return image_bytes


class PublishStep(Step):
    """Publish via the plugin registry (Facebook + any other active plugins)."""
    name = "publish"

    def __init__(self, registry: "PluginRegistry", brand: "Brand") -> None:
        self._registry = registry
        self._brand = brand

    def run(self, context: dict[str, Any]) -> Any:
        caption = context.get("caption") or context.get("content_result", {}).get("caption", "")
        image_bytes = context.get("image_bytes")

        if not caption:
            raise RuntimeError("No caption available — cannot publish")

        # Pass brand context to plugins so they know which account to post to
        # Plugins should be updated to accept a brand object or workspace_id
        results = self._registry.publish_all(caption, image_bytes=image_bytes, brand=self._brand)
        context["publish_results"] = results

        if not results:
            raise RuntimeError("No plugins registered or failed to publish.")

        failed_all = all(not r.get("ok") for r in results.values())
        if failed_all:
            errors = {k: v.get("error", "unknown") for k, v in results.items()}
            raise RuntimeError(f"All plugins failed to publish: {errors}")

        logger.info("PostWorkflow: published to %s", [k for k, v in results.items() if v.get("ok")])
        return results


class RecordAnalyticsStep(Step):
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
    name = "post_publish"

    def __init__(
        self,
        content_engine: "ContentEngine",
        registry: "PluginRegistry",
        analytics_repo: "AnalyticsRepository",
        bus: "EventBus",
        brand: "Brand",
        ai_profile: "AIProfile",
        category: str | None = None,
    ) -> None:
        super().__init__(
            steps=[
                SelectTopicStep(brand, category),
                GenerateContentStep(content_engine, brand, ai_profile),
                RenderImageStep(content_engine),
                PublishStep(registry, brand),
                RecordAnalyticsStep(analytics_repo),
                EmitEventStep(bus),
            ]
        )
