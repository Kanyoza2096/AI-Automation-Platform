"""
Post Publish Workflow — Architecture §22.

Full lifecycle workflow for post creation and publishing.
Used by manual triggers, scheduled posts, and durable outbox execution.

Stages (8):
  1. Select Topic          — from brand KB or custom_topics
  2. Knowledge Context     — retrieve relevant KB facts
  3. Generate Content      — LLM caption + Cloudflare image via ContentEngine
  4. Validate Content      — ContentValidator (reject on policy violations)
  5. Render Image          — verify MediaArtifact is ready
  6. Publish               — governed canonical publish via Action Kernel
  7. Record Analytics      — persist metrics
  8. Emit Event            — broadcast PostPublishedEvent

Architecture §5  — MediaArtifact Integration
Architecture §6  — Synchronous Rendering
Architecture §21 — AI Pipeline Hardening

V1.9 (2026-10-01) — STRUCTURED STEP FAILURES:
  PublishStep.async_run raises workflows.base.StepFailure instead of
  RuntimeError/RenderFailedError. The pipeline's structured failure —
  code, error, decision_id, pipeline_trace, reconciliation_required —
  is preserved through the step boundary and appears in the API response.
  Previously every failure collapsed to a bare RuntimeError("..."), which
  execute_async reduced to StepResult(error=str), which the API envelope
  reduced again to "Publish step failed". Four layers of collapse; the
  operator had no way to diagnose a failure without grepping logs for
  the job_id.

V1.8 (2026-10-01) — DIRECTION-BASED TOPIC GENERATION:
  SelectTopicStep.run checks whether the brand declares a topic_direction
  and, if so, uses the AI fabric to generate a topic from it. Falls
  through to selection on any failure.

V1.7 (2026-09-29) — PUBLISH RESULT CONTRACT NORMALIZATION:
  PublishStep.async_run no longer treats status="ok" as a failure.

V1.6 (2026-09-28) — ASYNC-NATIVE STEPS.
"""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING


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
    """Pick a topic — either generated from the brand's direction or selected.

    V1.8 — Direction-based generation runs first when the brand declares a
    topic_direction. Falls through to selection on any failure so the
    workflow never blocks on AI availability.
    """
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

        # ── Direction-based generation (V1.8) ───────────────────────────
        if getattr(selector, "has_direction", False):
            recent = context.get("recent_topics") or []
            generated = selector.generate_from_direction(recent=recent)
            if generated:
                context["topic"] = generated
                context["category"] = "generated"
                logger.info(
                    "PostWorkflow: topic='%s' category='generated' brand='%s' "
                    "(direction)",
                    generated, self._brand.name,
                )
                return {"topic": generated, "category": "generated"}
            logger.info(
                "PostWorkflow: direction generation unavailable for brand '%s'; "
                "falling back to selection",
                self._brand.name,
            )

        # ── Selection fallback (existing behavior) ──────────────────────
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

    V1.6 — Async-native. KnowledgeBase.search() is async.
    continue_on_failure=True so a missing or empty KB never blocks the
    pipeline — the content engine will still generate without context.
    """
    name = "knowledge_context"
    continue_on_failure = True

    def __init__(self, brand: "Brand", knowledge_repo=None) -> None:
        self._brand = brand
        self._repo = knowledge_repo

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
            facts = [entry.content for entry, _score in (results or [])]
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

    def run(self, context: dict[str, Any]) -> Any:
        raise RuntimeError(
            "KnowledgeContextStep must be executed via Workflow.execute_async()."
        )


# ── 2. Generate Content ───────────────────────────────────────────────
class GenerateContentStep(Step):
    """Call ContentEngine to produce caption + MediaArtifact.

    V1.6 — Async-native. ContentEngine.generate_post is async.
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

    async def async_run(self, context: dict[str, Any]) -> Any:
        topic = context.get("topic", "")
        knowledge_context = context.get("knowledge_context", "")

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

    def run(self, context: dict[str, Any]) -> Any:
        raise RuntimeError(
            "GenerateContentStep must be executed via Workflow.execute_async()."
        )


# ── 3. Validate Content ───────────────────────────────────────────────
class ValidateContentStep(Step):
    """Validate AI-generated content before proceeding.

    V1.6 — Async-native. ContentValidator.validate is async.
    """
    name = "validate_content"

    def __init__(self, validator: "ContentValidator" = None) -> None:
        from services.content_validator import ContentValidator
        self._validator = validator or ContentValidator()

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

    def run(self, context: dict[str, Any]) -> Any:
        raise RuntimeError(
            "ValidateContentStep must be executed via Workflow.execute_async()."
        )


# ── 4. Render Image ───────────────────────────────────────────────────
class RenderImageStep(Step):
    """Check media readiness. Render already happened in ContentEngine."""
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

        if media_artifact.is_valid:
            context["image_bytes"] = media_artifact.bytes if media_artifact.bytes else None
            logger.info(
                "PostWorkflow: media ready — %s, %dKB",
                media_artifact.source, media_artifact.size_kb,
            )
            return media_artifact.to_dict()

        if media_artifact.is_text_only:
            context["image_bytes"] = None
            logger.info("PostWorkflow: text-only post — no media needed.")
            return media_artifact.to_dict()

        if media_artifact.is_pending:
            logger.error(
                "PostWorkflow: UNEXPECTED RENDERING status — "
                "render should have completed synchronously."
            )
            context["image_bytes"] = None
            return media_artifact.to_dict()

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

    V1.9 — Raises workflows.base.StepFailure instead of RuntimeError so the
    pipeline's structured failure (code, decision_id, trace, reconciliation
    flag) survives to the API envelope. See module docstring.
    """
    name = "publish"

    _SUCCESS_STATUSES = frozenset({"succeeded", "ok", "success", "published"})
    _UNCERTAIN_STATUSES = frozenset({"uncertain", "in_progress", "pending"})

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
            raise StepFailure(
                message="No caption available — cannot publish",
                code="publish_no_caption",
                detail={
                    "brand_id": getattr(self._brand, "id", None),
                    "brand_name": self._brand.name,
                    "topic": context.get("topic", ""),
                },
            )

        if media_artifact and media_artifact.has_failed:
            raise StepFailure(
                message=f"Media render failed: {media_artifact.error}",
                code="publish_media_render_failed",
                detail={
                    "brand_id": getattr(self._brand, "id", None),
                    "brand_name": self._brand.name,
                    "media_error": media_artifact.error,
                    "media_error_code": media_artifact.error_code,
                },
            )

        if media_artifact and media_artifact.is_pending:
            raise StepFailure(
                message="Media still rendering — should have completed synchronously",
                code="publish_media_pending",
                detail={
                    "brand_id": getattr(self._brand, "id", None),
                    "brand_name": self._brand.name,
                },
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
        raw_status = str(result.get("status") or "").strip().lower()

        provider_post_id = None
        if isinstance(output, dict):
            provider_post_id = (
                output.get("platform_post_id")
                or output.get("post_id")
                or output.get("provider_object_id")
            )
        provider_post_id = (
            provider_post_id
            or result.get("provider_object_id")
            or result.get("platform_post_id")
        )

        output_ok = bool(output.get("ok")) if isinstance(output, dict) else False
        is_success = (
            raw_status in self._SUCCESS_STATUSES
            and (output_ok or bool(provider_post_id))
        )
        is_uncertain = raw_status in self._UNCERTAIN_STATUSES

        publish_result = {
            **output,
            "action_status": result.get("status"),
            "action_intent_id": result.get("intent_id"),
            "action_execution_id": result.get("execution_id"),
            "governed": True,
        }
        if provider_post_id and not publish_result.get("post_id"):
            publish_result["post_id"] = provider_post_id
        if provider_post_id and not publish_result.get("platform_post_id"):
            publish_result["platform_post_id"] = provider_post_id

        context["publish_result"] = publish_result

        if is_success:
            context["publish_results"] = {platform: publish_result}
            logger.info(
                "PostWorkflow: publish succeeded for brand='%s' platform=%s post_id=%s",
                self._brand.name, platform, provider_post_id or "<unknown>",
            )
            return publish_result

        if is_uncertain:
            publish_result.setdefault("reconciliation_required", True)
            context["publish_results"] = {}
            logger.warning(
                "PostWorkflow: publish uncertain for brand='%s' platform=%s err=%r",
                self._brand.name, platform, publish_result.get("error"),
            )
            return publish_result

        # ── Non-success, non-uncertain: propagate structured failure ────
        #
        # The pipeline already computed a rich failure object (code, error,
        # trace, decision_id, reconciliation_required). Collapsing it to a
        # bare RuntimeError discards the only signals an operator has to
        # diagnose the failure from the API response. Raise StepFailure so
        # the structure survives to the workflow envelope.
        context["publish_results"] = {}

        code = (
            publish_result.get("code")
            or result.get("code")
            or "publish_rejected"
        )
        message = (
            publish_result.get("error")
            or result.get("error")
            or "Governed canonical publish rejected"
        )

        logger.error(
            "PostWorkflow: publish failed for brand='%s' platform=%s "
            "code=%s reason=%r",
            self._brand.name, platform, code, message,
        )

        raise StepFailure(
            message=message,
            code=code,
            detail={
                "decision_id": decision_id,
                "platform": platform,
                "brand_id": getattr(self._brand, "id", None),
                "brand_name": self._brand.name,
                "raw_status": raw_status,
                "pipeline_trace": publish_result.get("trace"),
                "reconciliation_required": publish_result.get(
                    "reconciliation_required"
                ),
                "platform_post_id": provider_post_id,
            },
        )

    def run(self, context: dict[str, Any]) -> Any:
        """Synchronous compatibility path for non-ASGI callers."""
        import asyncio
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.async_run(context))
        raise RuntimeError(
            "PublishStep.run() cannot be called inside a running event loop; "
            "use execute_async()/async_run()"
        )


# ── 6. Record Analytics + Emit Events ────────────────────────────────
class RecordAnalyticsStep(Step):
    """Record publish metrics to analytics repository.

    V1.6 — Analytics repository method is sync (`def record_post_published`).
    Keep run() sync.
    """
    name = "record_analytics"
    continue_on_failure = True

    def __init__(self, analytics_repo: "AnalyticsRepository") -> None:
        self._repo = analytics_repo

    def run(self, context: dict[str, Any]) -> Any:
        topic = context.get("topic", "")
        if context.get("publish_result", {}).get("queued"):
            return {"recorded": 0, "queued": True}

        publish_results = context.get("publish_results", {})
        recorded = 0
        for platform, result in publish_results.items():
            if result.get("ok"):
                self._repo.record_post_published(
                    topic=topic,
                    platform=platform,
                    post_id=result.get("post_id", ""),
                )
                recorded += 1
        return {"recorded": recorded}


class EmitEventStep(Step):
    """Emit PostPublishedEvent to the event bus.

    V1.6 — Async-native. EventBus.publish is async.
    """
    name = "emit_event"
    continue_on_failure = True

    def __init__(self, bus: "EventBus") -> None:
        self._bus = bus

    async def async_run(self, context: dict[str, Any]) -> Any:
        from core.events import PostPublishedEvent

        topic = context.get("topic", "")
        if context.get("publish_result", {}).get("queued"):
            return {"emitted": 0, "queued": True}

        publish_results = context.get("publish_results", {})
        content_result = context.get("content_result", {})
        caption = content_result.get("caption", "")

        emitted = 0
        for platform, result in publish_results.items():
            if not result.get("ok"):
                continue
            await self._bus.publish(
                PostPublishedEvent(
                    post_id=result.get("post_id", ""),
                    topic=topic,
                    platform=platform,
                    caption_preview=caption,
                )
            )
            emitted += 1

        return {"emitted": emitted}

    def run(self, context: dict[str, Any]) -> Any:
        raise RuntimeError(
            "EmitEventStep must be executed via Workflow.execute_async()."
        )


# ── Full Post Publish Workflow ────────────────────────────────────────
class PostPublishWorkflow(Workflow):
    """8-stage workflow.

    Stages:
      1. Select Topic          — from brand KB, custom_topics, or AI-generated
      2. Knowledge Context     — retrieve relevant KB facts
      3. Generate Content      — LLM caption + Cloudflare image
      4. Validate Content      — ContentValidator
      5. Render Image          — verify MediaArtifact
      6. Publish               — governed canonical publish
      7. Record Analytics      — persist metrics
      8. Emit Event            — broadcast PostPublishedEvent
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
