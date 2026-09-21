"""
Post Publish Workflow — Architecture §22.

Simplified 7-stage workflow for post creation and publishing.
Used by manual triggers and simpler automation paths.

Architecture §5 — MediaArtifact Integration:
  Media flows as typed MediaArtifact through all stages.
  Never None. Never ambiguous.

Architecture §6 — Synchronous Rendering:
  ContentEngine._render_image() blocks until rendering completes.
  MediaArtifact is always MEDIA_READY or RENDER_FAILED by stage 4.
  No async gaps. No duplicate posts from RenderQueue workers.

Architecture §21 — AI Pipeline Hardening:
  Content validated before rendering.
  Raw AI output never flows directly to publishing.

v4.1.2 — Async/Sync Bridge for content steps:
  ContentEngine.generate_post and KnowledgeBase.search are async-native.
  The workflow runner calls Step.run() synchronously from worker threads.
  Both steps now provide a sync `run` (via asyncio.run) and an `async_run`
  so they work from either context. Previously they called the async
  methods without awaiting, which produced un-awaited coroutines and
  halted the workflow at generate_content.

v4.1.3 — Async/Sync Bridge for validate_content:
  ContentValidator.validate is async-native (v3.0). ValidateContentStep
  was still calling it synchronously and reading `.is_rejected` on the
  returned coroutine, halting the workflow with
  "'coroutine' object has no attribute 'is_rejected'". This step now
  provides the same sync `run` + `async_run` bridge used by the other
  async-native steps.
"""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from workflows.base import Step, Workflow
from models.media_artifact import MediaArtifact, MediaStatus
from core.errors import (
    AIValidationError,
    RenderFailedError,
    KanyozaError,
)

if TYPE_CHECKING:
    from services.content_engine import ContentEngine
    from services.content_validator import ContentValidator
    from plugins.base import PluginRegistry
    from core.event_bus import EventBus
    from repositories.analytics_repository import AnalyticsRepository

logger = logging.getLogger(__name__)


# ── 1. Select Topic ───────────────────────────────────────────────────
class SelectTopicStep(Step):
    """Pick a topic from the brand's Knowledge Base (or custom_topics)."""
    name = "select_topic"

    def __init__(
        self,
        brand: "Brand",
        category: str | None = None,
        knowledge_repo=None,
    ) -> None:
        self._brand = brand
        self._category = category
        self._knowledge_repo = knowledge_repo

    def run(self, context: dict[str, Any]) -> Any:
        from domain.content.topic_selector import TopicSelector

        selector = TopicSelector(
            brand=self._brand,
            knowledge_repo=self._knowledge_repo,
        )

        if not selector.has_topics():
            logger.warning(
                "PostWorkflow: brand '%s' has no KB entries and no custom_topics. "
                "Add documents to the Knowledge Base so topics can be picked from it.",
                self._brand.name,
            )

        topic, cat = selector.pick(self._category)
        context["topic"] = topic
        context["category"] = cat
        logger.info(
            "PostWorkflow: topic='%s' category='%s' brand='%s'",
            topic, cat, self._brand.name,
        )
        return {"topic": topic, "category": cat}


# ── 1b. Knowledge Context ─────────────────────────────────────────────
class KnowledgeContextStep(Step):
    """Retrieve relevant KB facts for the chosen topic.

    Searches the brand's Knowledge Base using TF-IDF cosine similarity and
    injects the top matching entries into the context so the prompt builder
    can ground the LLM in brand-specific knowledge.

    Marked continue_on_failure=True so a missing or empty KB never blocks
    the pipeline — the content engine will still generate without context.

    v4.1.2 — KnowledgeBase.search is async. This step provides both a sync
    `run` (via asyncio.run) and an `async_run` so it works from either
    context. Previously the call returned an un-awaited coroutine and the
    step logged "KB lookup failed ... 'coroutine' object is not iterable".
    """
    name = "knowledge_context"
    continue_on_failure = True

    def __init__(self, brand: "Brand", knowledge_repo=None) -> None:
        self._brand = brand
        self._repo = knowledge_repo

    # ── Sync bridge ───────────────────────────────────────────────────

    def run(self, context: dict[str, Any]) -> Any:
        import asyncio
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.async_run(context))
        raise RuntimeError(
            "KnowledgeContextStep.run() cannot be called inside a running event loop; "
            "use execute_async()/async_run()"
        )

    # ── Async implementation ──────────────────────────────────────────

    async def async_run(self, context: dict[str, Any]) -> Any:
        topic = context.get("topic", "")
        if not topic or not self._repo:
            context["knowledge_context"] = ""
            return []

        try:
            from services.knowledge_base import KnowledgeBase
            kb = KnowledgeBase(
                brand_id=self._brand.id,
                workspace_id=self._brand.workspace_id,
                repository=self._repo,
            )
            results = await kb.search(topic, top_k=3)
            facts = [entry.content for entry, _score in results]
            knowledge_context = "\n".join(facts)
            context["knowledge_context"] = knowledge_context
            logger.info(
                "PostWorkflow: KB retrieved %d entries for topic='%s' brand='%s'",
                len(facts), topic[:60], self._brand.name,
            )
            return facts
        except Exception as exc:
            logger.warning(
                "PostWorkflow: KB lookup failed for brand='%s': %s",
                self._brand.name, exc,
            )
            context["knowledge_context"] = ""
            return []


# ── 2. Generate Content ───────────────────────────────────────────────
class GenerateContentStep(Step):
    """Call ContentEngine to produce caption + MediaArtifact.

    Passes the pre-assembled prompt (with KB context) if one was built by
    KnowledgeContextStep + PromptBuilder; otherwise lets ContentEngine
    build its own prompt so the step is fully self-contained.

    Architecture §6: ContentEngine now blocks until render completes.
    MediaArtifact is already MEDIA_READY or RENDER_FAILED when returned.

    v4.1.2 — ContentEngine.generate_post is async-native. This step provides
    both a sync `run` (via asyncio.run) and an `async_run` so it works from
    either context. Previously the call returned an un-awaited coroutine and
    the workflow halted with "'coroutine' object has no attribute 'get'".
    """
    name = "generate_content"

    def __init__(
        self,
        content_engine: "ContentEngine",
        brand: "Brand",
        ai_profile: "AIProfile",
        prompt_builder=None,
        container=None,
    ) -> None:
        self._engine = content_engine
        self._brand = brand
        self._ai_profile = ai_profile
        self._prompt_builder = prompt_builder

    # ── Sync bridge ───────────────────────────────────────────────────

    def run(self, context: dict[str, Any]) -> Any:
        import asyncio
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.async_run(context))
        raise RuntimeError(
            "GenerateContentStep.run() cannot be called inside a running event loop; "
            "use execute_async()/async_run()"
        )

    # ── Async implementation ──────────────────────────────────────────

    async def async_run(self, context: dict[str, Any]) -> Any:
        topic = context.get("topic", "")
        knowledge_context = context.get("knowledge_context", "")

        # Build a KB-enriched prompt when we have context and a builder
        base_prompt: str | None = None
        if self._prompt_builder and knowledge_context:
            try:
                base_prompt = self._prompt_builder.build_post_prompt(
                    brand=self._brand,
                    ai_profile=self._ai_profile,
                    topic=topic,
                    knowledge_context=knowledge_context,
                )
                logger.info(
                    "PostWorkflow: prompt assembled with KB context (%d chars) for brand='%s'",
                    len(knowledge_context), self._brand.name,
                )
            except Exception as exc:
                logger.warning(
                    "PostWorkflow: prompt assembly failed (%s) — continuing without KB context",
                    exc,
                )
                base_prompt = None

        result = await self._engine.generate_post(
            brand=self._brand,
            ai_profile=self._ai_profile,
            topic=topic,
            base_prompt=base_prompt,
        )
        if not result:
            raise AIValidationError(
                message=f"ContentEngine returned no result for '{topic}'",
                context={"brand": self._brand.name, "topic": topic},
            )

        context["content_result"] = result
        supplied_caption = context.get("scheduled_caption")
        context["caption"] = (
            str(supplied_caption) if supplied_caption is not None else result.get("caption", "")
        )
        context["image_bytes"] = result.get("image_bytes")
        context["media_artifact"] = result.get(
            "media_artifact", MediaArtifact.no_media()
        )

        media_status = context["media_artifact"].status.value
        logger.info(
            "PostWorkflow: content generated — brand='%s', caption_len=%d, media=%s",
            self._brand.name, len(context["caption"]), media_status,
        )
        return result


# ── 3. Validate Content ───────────────────────────────────────────────
class ValidateContentStep(Step):
    """Validate AI-generated content before proceeding.

    v4.1.3 — ContentValidator.validate is async-native (v3.0). This step
    provides both a sync `run` (via asyncio.run) and an `async_run` so it
    works from either context. Previously the call returned an un-awaited
    coroutine and the workflow halted with
    "'coroutine' object has no attribute 'is_rejected'".
    """
    name = "validate_content"

    def __init__(self, validator: "ContentValidator" = None) -> None:
        from services.content_validator import ContentValidator
        self._validator = validator or ContentValidator()

    # ── Sync bridge ───────────────────────────────────────────────────

    def run(self, context: dict[str, Any]) -> Any:
        import asyncio
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.async_run(context))
        raise RuntimeError(
            "ValidateContentStep.run() cannot be called inside a running event loop; "
            "use execute_async()/async_run()"
        )

    # ── Async implementation ──────────────────────────────────────────

    async def async_run(self, context: dict[str, Any]) -> Any:
        caption = context.get("caption", "")
        topic = context.get("topic", "")
        content_result = context.get("content_result", {})

        content = {
            "title": content_result.get("title", topic),
            "caption": caption,
            "content_type": context.get("content_type", "social_post"),
            "hashtags": content_result.get("hashtags", []),
            "media_required": True,
        }

        brand = context.get("brand")
        platform = context.get("platform", "facebook")

        validation_result = await self._validator.validate(
            content=content,
            brand=brand,
            platform=platform,
            require_media=True,
        )

        context["validation_result"] = validation_result

        if validation_result.is_rejected:
            errors = validation_result.error_messages
            logger.error(
                "PostWorkflow: validation REJECTED — %s",
                "; ".join(errors),
            )
            raise AIValidationError(
                message=f"Content validation failed: {'; '.join(errors)}",
                context={"errors": errors, "topic": topic},
            )

        logger.info(
            "PostWorkflow: validation passed (warnings=%d)",
            validation_result.warnings_count,
        )
        return validation_result.to_dict()


# ── 4. Render Image ───────────────────────────────────────────────────
class RenderImageStep(Step):
    """Check media readiness. Render already happened in ContentEngine.

    Architecture §6: ContentEngine blocks until render completes.
    MediaArtifact is always MEDIA_READY, NO_MEDIA, or RENDER_FAILED.
    RENDERING status should never appear here.
    """
    name = "render_image"
    continue_on_failure = True

    def __init__(self, content_engine: "ContentEngine") -> None:
        self._engine = content_engine

    def run(self, context: dict[str, Any]) -> Any:
        media_artifact: MediaArtifact = context.get("media_artifact")

        if media_artifact is None:
            logger.warning("PostWorkflow: No MediaArtifact in context — creating NO_MEDIA")
            media_artifact = MediaArtifact.no_media()
            context["media_artifact"] = media_artifact
            context["image_bytes"] = None
            return media_artifact.to_dict()

        # Media ready
        if media_artifact.is_valid:
            context["image_bytes"] = media_artifact.bytes if media_artifact.bytes else None
            logger.info(
                "PostWorkflow: media ready — %s, %dKB",
                media_artifact.source, media_artifact.size_kb,
            )
            return media_artifact.to_dict()

        # Text-only
        if media_artifact.is_text_only:
            context["image_bytes"] = None
            logger.info("PostWorkflow: text-only post — no media needed.")
            return media_artifact.to_dict()

        # Should never happen — ContentEngine blocks until complete
        if media_artifact.is_pending:
            logger.error(
                "PostWorkflow: UNEXPECTED RENDERING status — "
                "render should have completed synchronously. "
                "This indicates a bug in ContentEngine._render_image()."
            )
            context["image_bytes"] = None
            return media_artifact.to_dict()

        # Render failed
        if media_artifact.has_failed:
            context["image_bytes"] = None
            logger.error(
                "PostWorkflow: render FAILED — %s (code=%s)",
                media_artifact.error, media_artifact.error_code,
            )
            return media_artifact.to_dict()

        logger.warning("PostWorkflow: unknown media status: %s", media_artifact.status)
        return media_artifact.to_dict()


# ── 5. Publish ────────────────────────────────────────────────────────
class PublishStep(Step):
    """Publish via the plugin registry with MediaArtifact awareness.

    Architecture §6: No text-only fallback from async gaps.
    RENDER_FAILED raises immediately. Every published post has its image.
    """

    name = "publish"

    def __init__(self, registry: "PluginRegistry", brand: "Brand", container=None) -> None:
        self._registry = registry
        self._brand = brand
        self._container = container

    async def async_run(self, context: dict[str, Any]) -> Any:
        caption = context.get("caption") or context.get(
            "content_result", {}
        ).get("caption", "")
        image_bytes = context.get("image_bytes")
        media_artifact: MediaArtifact = context.get("media_artifact")

        if not caption:
            raise RuntimeError("No caption available — cannot publish")

        # Render failed — raise immediately, no text-only fallback
        if media_artifact and media_artifact.has_failed:
            raise RenderFailedError(
                message=f"Media render failed: {media_artifact.error}",
                context={
                    "brand": self._brand.name,
                    "media_error": media_artifact.error,
                    "media_error_code": media_artifact.error_code,
                },
            )

        # Render still pending — should never happen
        if media_artifact and media_artifact.is_pending:
            raise RenderFailedError(
                message="Media still rendering — should have completed synchronously",
                context={"brand": self._brand.name},
            )

        from services.publish_orchestration import execute_governed_publish
        from core.publish_authorization import issue_internal_publish_decision
        workspace_id = getattr(self._brand, "workspace_id", "")
        job_id = context.get("job_id")
        platform = context.get("platform") or getattr(self._brand, "platform", None) or "facebook"
        risk_level = context.get("risk_level", "low")
        source = context.get("source", "workflow")
        decision_id = context.get("decision_id")
        if not decision_id:
            decision = issue_internal_publish_decision(
                workspace_id=workspace_id, source=source, risk_level=risk_level,
                brand_id=getattr(self._brand, "id", None), platform=platform,
            )
            decision_id = decision.decision_id
        media_url = getattr(media_artifact, "media_url", None) if media_artifact else None
        result = await execute_governed_publish(
            container=getattr(self, "_container", None),
            workspace_id=workspace_id, brand_id=getattr(self._brand, "id", None),
            platform=platform, topic=context.get("topic", ""), caption=caption,
            risk_level=risk_level, decision_id=decision_id, approved=True,
            automation_job_id=job_id, source=source, image_url=media_url,
            image_bytes=image_bytes, scheduled_post_id=context.get("scheduled_post_id"),
            idempotency_key=(
                f"scheduled:{context.get('scheduled_post_id')}"
                if context.get("scheduled_post_id") else None
            ),
        )
        output = result.get("output") if isinstance(result.get("output"), dict) else {}
        publish_result = {**output, "action_status": result.get("status"),
                          "action_intent_id": result.get("intent_id"),
                          "action_execution_id": result.get("execution_id"),
                          "governed": True}
        context["publish_result"] = publish_result
        context["publish_results"] = {platform: publish_result} if result.get("status") == "succeeded" and publish_result.get("ok") else {}
        if result.get("status") != "succeeded" or not publish_result.get("ok"):
            raise RuntimeError(publish_result.get("error") or "Governed canonical publish rejected")
        return publish_result


    def run(self, context: dict[str, Any]) -> Any:
        """Synchronous compatibility path for non-ASGI callers."""
        import asyncio
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.async_run(context))
        raise RuntimeError("PublishStep.run() cannot be called inside a running event loop; use execute_async()/async_run()")


# ── 6. Record Analytics + Emit Events ────────────────────────────────
class RecordAnalyticsStep(Step):
    """Record publish metrics to analytics repository."""
    name = "record_analytics"
    continue_on_failure = True

    def __init__(self, analytics_repo: "AnalyticsRepository") -> None:
        self._repo = analytics_repo

    def run(self, context: dict[str, Any]) -> Any:
        topic = context.get("topic", "")
        if context.get("publish_result", {}).get("queued"):
            return {"recorded": 0, "queued": True}
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
    """Emit PostPublishedEvent to the event bus."""
    name = "emit_event"
    continue_on_failure = True

    def __init__(self, bus: "EventBus") -> None:
        self._bus = bus

    def run(self, context: dict[str, Any]) -> Any:
        from core.events import PostPublishedEvent
        topic = context.get("topic", "")
        if context.get("publish_result", {}).get("queued"):
            return {"emitted": 0, "queued": True}
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


# ── Full Post Publish Workflow ────────────────────────────────────────
class PostPublishWorkflow(Workflow):
    """9-stage workflow: Topic → KB Context → Generate → Validate → Render →
    Publish → Record → Emit.

    The added KnowledgeContextStep (stage 2) ensures every post is grounded
    in the brand's Knowledge Base before the LLM prompt is assembled.

    Architecture §6 — Synchronous Rendering:
      ContentEngine blocks until render completes. Pipeline always has
      MEDIA_READY or RENDER_FAILED by stage 5. No async gaps.

    Architecture §22 — Workflow Lifecycle:
      CREATED → AI_GENERATING → CONTENT_VALIDATED →
      MEDIA_READY → PUBLISHING → PUBLISHED

    Stage contracts:
      1. Select Topic       → topic, category  (from KB or custom_topics)
      2. Knowledge Context  → knowledge_context (KB facts for this topic)
      3. Generate Content   → caption, media_artifact (prompt enriched with KB)
      4. Validate Content   → validation_result
      5. Render Image       → media_artifact status check
      6. Publish            → publish_results (with image, or raises)
      7. Record             → analytics
      8. Emit               → events
    """

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
        content_validator: "ContentValidator" = None,
        knowledge_repo=None,
        prompt_builder=None,
        container=None,
    ) -> None:
        self._container = container
        super().__init__(
            steps=[
                SelectTopicStep(brand, category, knowledge_repo=knowledge_repo),
                KnowledgeContextStep(brand, knowledge_repo=knowledge_repo),
                GenerateContentStep(
                    content_engine, brand, ai_profile,
                    prompt_builder=prompt_builder,
                ),
                ValidateContentStep(validator=content_validator),
                RenderImageStep(content_engine),
                PublishStep(registry, brand, container=container),
                RecordAnalyticsStep(analytics_repo),
                EmitEventStep(bus),
            ]
        )
