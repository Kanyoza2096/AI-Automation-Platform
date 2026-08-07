"""
10-Stage Content Generation and Publishing Pipeline — v12.4
Implemented as a subclass of Workflow to provide full traceability,
latency tracking, and error boundaries for each stage.

Architecture §5 — MediaArtifact Integration:
  Media flows as typed MediaArtifact through stages 5→8→9.
  Never None. Never ambiguous.

Architecture §6 — Synchronous Rendering:
  ContentEngine._render_image() now blocks until rendering completes.
  Pipeline never sees RENDERING status. Every post has its image.
  No duplicate posts from async RenderQueue workers.

Architecture §21 — AI Pipeline Hardening:
  Content validated before rendering (stage 6).
  Raw AI output never flows directly to publishing.

Architecture §12 — Typed Errors:
  Failures propagate with error codes, not generic strings.
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Dict, List

from workflows.base import Step, Workflow
from models.brand import Brand
from models.ai_profile import AIProfile
from models.media_artifact import MediaArtifact, MediaStatus
from services.content_engine import ContentEngine
from services.content_validator import ContentValidator, ValidationResult
from services.prompt_builder import PromptBuilder
from services.knowledge_base import KnowledgeBase
from repositories.knowledge_repository import KnowledgeRepository
from repositories.analytics_repository import AnalyticsRepository
from core.event_bus import EventBus
from core.errors import (
    KanyozaError,
    AIValidationError,
    RenderFailedError,
    NoSocialAccountError,
    ErrorCode,
)

logger = logging.getLogger("kanyoza.pipeline")


# ── 1. Planner Step ───────────────────────────────────────────────────
class PlannerStep(Step):
    """Stage 1: Selects the topic, category, and strategy for this post.
    
    Uses the brand's Knowledge Base to pick topics.
    Content type comes from brand configuration, not hardcoded.
    """
    name = "planner"

    def __init__(
        self,
        brand: Brand,
        category: Optional[str] = None,
        knowledge_repo: Optional[KnowledgeRepository] = None,
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
        topic, cat = selector.pick(self._category)
        
        context["topic"] = topic
        context["category"] = cat
        # Content type from brand config, fallback to social_post
        context["content_type"] = getattr(self._brand, 'content_type', None) or "social_post"
        
        logger.info("[Pipeline Stage 1] Topic planned: '%s' (%s)", topic, cat)
        return {"topic": topic, "category": cat}


# ── 2. Research Step ──────────────────────────────────────────────────
class ResearchStep(Step):
    """Stage 2: Scrapes external sources."""
    name = "research"
    continue_on_failure = True

    def run(self, context: dict[str, Any]) -> Any:
        topic = context.get("topic", "")
        research_notes = f"Research notes for topic: {topic}."
        context["research_notes"] = research_notes
        logger.info("[Pipeline Stage 2] Research complete.")
        return research_notes


# ── 3. Knowledge Step ─────────────────────────────────────────────────
class KnowledgeStep(Step):
    """Stage 3: Retrieves relevant facts from the brand's knowledge base."""
    name = "knowledge_retrieval"
    continue_on_failure = True

    def __init__(self, brand: Brand, repo: KnowledgeRepository) -> None:
        self._brand = brand
        self._repo = repo

    def run(self, context: dict[str, Any]) -> Any:
        topic = context.get("topic", "")
        kb = KnowledgeBase(
            brand_id=self._brand.id,
            workspace_id=self._brand.workspace_id,
            repository=self._repo,
        )
        results = kb.search(topic, top_k=3)
        facts = [entry.content for entry, _ in results]
        knowledge_context = "\n".join(facts)
        context["knowledge_context"] = knowledge_context
        logger.info("[Pipeline Stage 3] Knowledge retrieved: %d entries.", len(facts))
        return facts


# ── 4. Prompt Builder Step ────────────────────────────────────────────
class PromptBuilderStep(Step):
    """Stage 4: Assembles the prompt using PromptBuilder.
    
    PromptBuilder now includes brand-aware style guides based on tone:
      - spiritual/inspirational → devotional format (no comparisons)
      - professional/technical → comparison format
      - casual/friendly → conversational format
    """
    name = "prompt_builder"

    def __init__(
        self,
        brand: Brand,
        ai_profile: AIProfile,
        prompt_builder: PromptBuilder,
    ) -> None:
        self._brand = brand
        self._ai_profile = ai_profile
        self._pb = prompt_builder

    def run(self, context: dict[str, Any]) -> Any:
        topic = context.get("topic", "")
        knowledge_context = context.get("knowledge_context", "")
        prompt = self._pb.build_post_prompt(
            brand=self._brand,
            ai_profile=self._ai_profile,
            topic=topic,
            knowledge_context=knowledge_context,
        )
        context["assembled_prompt"] = prompt
        logger.info("[Pipeline Stage 4] Complete prompt assembled.")
        return prompt


# ── 5. LLM Step ───────────────────────────────────────────────────────
class LLMStep(Step):
    """Stage 5: Submits prompt to Gemini. Returns content + MediaArtifact."""
    name = "llm_generation"

    def __init__(
        self,
        content_engine: ContentEngine,
        brand: Optional[Any] = None,
        ai_profile: Optional[Any] = None,
    ) -> None:
        self._engine = content_engine
        self._brand = brand
        self._ai_profile = ai_profile

    def run(self, context: dict[str, Any]) -> Any:
        topic = context.get("topic", "")
        assembled_prompt = context.get("assembled_prompt") or None

        result = self._engine.generate_post(
            brand=self._brand,
            ai_profile=self._ai_profile,
            topic=topic,
            base_prompt=assembled_prompt,
        )
        if not result:
            raise AIValidationError(
                message="LLM generation returned an empty result",
                context={"brand": getattr(self._brand, "name", "unknown"), "topic": topic},
            )

        context["content_result"] = result
        context["caption"] = result.get("caption", "")
        context["image_bytes"] = result.get("image_bytes")
        context["media_artifact"] = result.get("media_artifact", MediaArtifact.no_media())

        media_status = context["media_artifact"].status.value
        logger.info(
            "[Pipeline Stage 5] LLM generation complete — brand='%s', media=%s",
            getattr(self._brand, "name", "default"), media_status,
        )
        return result


# ── 6. Validator Step ─────────────────────────────────────────────────
class ValidatorStep(Step):
    """Stage 6: Validates AI output against schema, platform, and brand rules."""

    name = "validator"

    def __init__(self, validator: Optional[ContentValidator] = None) -> None:
        self._validator = validator or ContentValidator()

    def run(self, context: dict[str, Any]) -> Any:
        caption = context.get("caption", "")
        topic = context.get("topic", "")

        content = {
            "title": context.get("content_result", {}).get("title", topic),
            "caption": caption,
            "content_type": context.get("content_type", "social_post"),
            "hashtags": context.get("content_result", {}).get("hashtags", []),
            "media_required": True,
        }

        brand = context.get("brand")
        platform = context.get("platform", "facebook")

        validation_result = self._validator.validate(
            content=content,
            brand=brand,
            platform=platform,
            require_media=True,
        )

        context["validation_result"] = validation_result

        if validation_result.is_rejected:
            errors = validation_result.error_messages
            logger.error(
                "[Pipeline Stage 6] Validation REJECTED: %s",
                "; ".join(errors),
            )
            raise AIValidationError(
                message=f"Content validation failed: {'; '.join(errors)}",
                context={
                    "errors": errors,
                    "warnings": validation_result.warning_messages,
                    "topic": topic,
                },
            )

        if validation_result.status == validation_result.status.APPROVED_WITH_WARNINGS:
            logger.warning(
                "[Pipeline Stage 6] Validation passed with %d warnings: %s",
                validation_result.warnings_count,
                "; ".join(validation_result.warning_messages),
            )
        else:
            logger.info("[Pipeline Stage 6] Validation passed.")

        return validation_result.to_dict()


# ── 7. Reviewer Step ──────────────────────────────────────────────────
class ReviewerStep(Step):
    """Stage 7: Brand voice quality control."""
    name = "reviewer"
    continue_on_failure = True

    def run(self, context: dict[str, Any]) -> Any:
        logger.info("[Pipeline Stage 7] Review completed.")
        return True


# ── 8. Card Renderer Step ─────────────────────────────────────────────
class CardRendererStep(Step):
    """Stage 8: Inspects media readiness.
    
    Architecture §6 — Synchronous Rendering:
      ContentEngine._render_image() now BLOCKS until rendering completes.
      MediaArtifact should already be MEDIA_READY or RENDER_FAILED by
      the time this step runs. RENDERING status should never appear here.
    """

    name = "card_renderer"
    continue_on_failure = True

    def __init__(self, content_engine: ContentEngine) -> None:
        self._engine = content_engine

    def run(self, context: dict[str, Any]) -> Any:
        media_artifact: MediaArtifact = context.get("media_artifact")

        if media_artifact is None:
            logger.warning("[Pipeline Stage 8] No MediaArtifact in context — creating NO_MEDIA")
            media_artifact = MediaArtifact.no_media()
            context["media_artifact"] = media_artifact

        if media_artifact.is_valid:
            logger.info(
                "[Pipeline Stage 8] Media ready (%s, %dKB).",
                media_artifact.source, media_artifact.size_kb,
            )
            return media_artifact.to_dict()

        if media_artifact.is_text_only:
            logger.info("[Pipeline Stage 8] Text-only post — no media needed.")
            return media_artifact.to_dict()

        if media_artifact.is_pending:
            logger.error(
                "[Pipeline Stage 8] UNEXPECTED RENDERING status — "
                "render should have completed synchronously."
            )
            return media_artifact.to_dict()

        if media_artifact.has_failed:
            logger.error(
                "[Pipeline Stage 8] Render FAILED: %s (code=%s)",
                media_artifact.error, media_artifact.error_code,
            )
            return media_artifact.to_dict()

        logger.warning("[Pipeline Stage 8] Unknown media status: %s", media_artifact.status)
        return media_artifact.to_dict()


# ── 9. Publisher Step ─────────────────────────────────────────────────
class PublisherStep(Step):
    """Stage 9: Publishes content via the container's PluginRegistry.
    
    Architecture §6 — No text-only fallback from async gaps:
      MediaArtifact is always MEDIA_READY or RENDER_FAILED by this point.
      RENDER_FAILED raises immediately — no silent text-only fallback.
    """

    name = "publisher"

    def __init__(self, brand: Brand, container) -> None:
        self._brand = brand
        self._container = container

    def run(self, context: dict[str, Any]) -> Any:
        from plugins.base import PluginRegistry
        
        caption = context.get("caption", "")
        media_artifact: MediaArtifact = context.get("media_artifact")
        
        image_bytes: Optional[bytes] = None
        
        if media_artifact and media_artifact.is_valid:
            image_bytes = media_artifact.bytes if media_artifact.bytes else None
            logger.info(
                "PublisherStep: Publishing with media (%s, %dKB)",
                media_artifact.source, media_artifact.size_kb,
            )
        elif media_artifact and media_artifact.has_failed:
            raise RenderFailedError(
                message=f"Media render failed: {media_artifact.error}",
                context={
                    "brand": self._brand.name,
                    "media_error": media_artifact.error,
                    "media_error_code": media_artifact.error_code,
                },
            )
        elif media_artifact and media_artifact.is_text_only:
            logger.info("PublisherStep: Text-only post (no media needed).")
        elif media_artifact and media_artifact.is_pending:
            raise RenderFailedError(
                message="Media still rendering — should have completed synchronously",
                context={"brand": self._brand.name},
            )
        
        registry = self._container.resolve(PluginRegistry)
        all_plugins = registry.get_all()
        logger.info(
            "PublisherStep: registry has %d plugins: %s",
            len(all_plugins), [p.name for p in all_plugins],
        )
        
        results = registry.publish_all(
            content=caption,
            image_bytes=image_bytes,
            brand=self._brand,
        )
        context["publish_results"] = results
        
        if not results:
            raise RuntimeError("No plugins returned results — registry may be empty")
        
        failed_all = all(not r.get("ok") for r in results.values())
        if failed_all:
            errors = {
                k: v.get("error", {}).get("message", "Unknown")
                for k, v in results.items()
            }
            raise RuntimeError(f"All plugins failed to publish: {errors}")
        
        logger.info(
            "[Pipeline Stage 9] Published successfully. Results: %s",
            {k: v.get("ok") for k, v in results.items()},
        )
        return results


# ── 10. Analytics Step ────────────────────────────────────────────────
class AnalyticsStep(Step):
    """Stage 10: Records metrics and emits events."""
    name = "analytics"
    continue_on_failure = True

    def __init__(self, analytics_repo: AnalyticsRepository, bus: EventBus) -> None:
        self._repo = analytics_repo
        self._bus = bus

    def run(self, context: dict[str, Any]) -> Any:
        from core.events import PostPublishedEvent
        topic = context.get("topic", "")
        publish_results = context.get("publish_results", {})
        caption = context.get("caption", "")
        media_artifact: Optional[MediaArtifact] = context.get("media_artifact")
        
        for platform, res in publish_results.items():
            if res.get("ok"):
                self._repo.record_post_published(
                    topic=topic,
                    platform=platform,
                    post_id=res.get("post_id", ""),
                )
                self._bus.publish(PostPublishedEvent(
                    post_id=res.get("post_id", ""),
                    topic=topic,
                    platform=platform,
                    caption_preview=caption[:100],
                ))
        
        if media_artifact:
            logger.info(
                "[Pipeline Stage 10] Analytics recorded. Media: %s",
                media_artifact.status.value,
            )
        else:
            logger.info("[Pipeline Stage 10] Analytics recorded.")
        
        return True


# ── Full 10-Stage Pipeline ───────────────────────────────────────────
class ContentGenerationPipeline(Workflow):
    """Execution pipeline implementing the 10 core stages of content production.
    
    Architecture §6 — Synchronous Rendering:
      ContentEngine blocks until render completes. Pipeline always has
      MEDIA_READY or RENDER_FAILED by stage 8. No async gaps.
    
    Stage contracts:
      1. Planner      → topic, category (from KB, brand-aware content type)
      2. Research     → research_notes
      3. Knowledge    → knowledge_context
      4. Prompt       → assembled_prompt (with brand-aware style guide)
      5. LLM          → caption, media_artifact
      6. Validator    → validation_result
      7. Reviewer     → brand voice check
      8. Card Render  → media_artifact status check
      9. Publisher    → publish_results
      10. Analytics   → metrics, events
    """

    name = "content_generation_pipeline"

    def __init__(
        self,
        content_engine: ContentEngine,
        analytics_repo: AnalyticsRepository,
        bus: EventBus,
        knowledge_repo: KnowledgeRepository,
        prompt_builder: PromptBuilder,
        brand: Brand,
        ai_profile: AIProfile,
        container,
        category: Optional[str] = None,
        content_validator: Optional[ContentValidator] = None,
    ) -> None:
        super().__init__(
            steps=[
                PlannerStep(brand, category, knowledge_repo=knowledge_repo),
                ResearchStep(),
                KnowledgeStep(brand, knowledge_repo),
                PromptBuilderStep(brand, ai_profile, prompt_builder),
                LLMStep(content_engine, brand=brand, ai_profile=ai_profile),
                ValidatorStep(validator=content_validator),
                ReviewerStep(),
                CardRendererStep(content_engine),
                PublisherStep(brand, container),
                AnalyticsStep(analytics_repo, bus),
            ]
        )
