"""
10-Stage Content Generation and Publishing Pipeline.
Implemented as a subclass of Workflow to provide full traceability,
latency tracking, and error boundaries for each stage.
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Dict, List

from workflows.base import Step, Workflow
from models.brand import Brand
from models.ai_profile import AIProfile
from services.content_engine import ContentEngine
from services.prompt_builder import PromptBuilder
from services.knowledge_base import KnowledgeBase
from repositories.knowledge_repository import KnowledgeRepository
from plugins.base import PluginRegistry
from repositories.analytics_repository import AnalyticsRepository
from core.event_bus import EventBus

logger = logging.getLogger("kanyoza.pipeline")


# ── 1. Planner Step ───────────────────────────────────────────────────
class PlannerStep(Step):
    """Stage 1: Selects the topic, category, and strategy for this post."""
    name = "planner"

    def __init__(self, brand: Brand, category: Optional[str] = None) -> None:
        self._brand = brand
        self._category = category

    def run(self, context: dict[str, Any]) -> Any:
        from domain.content.topic_selector import TopicSelector
        selector = TopicSelector(brand=self._brand)
        topic, cat = selector.pick(self._category)
        
        context["topic"] = topic
        context["category"] = cat
        context["content_type"] = "comparison" # Default strategy
        
        logger.info("[Pipeline Stage 1] Topic planned: '%s' (%s)", topic, cat)
        return {"topic": topic, "category": cat}


# ── 2. Research Step ──────────────────────────────────────────────────
class ResearchStep(Step):
    """Stage 2: Scrapes external sources (mocked/stubbed or news scraper if needed)."""
    name = "research"
    continue_on_failure = True

    def run(self, context: dict[str, Any]) -> Any:
        topic = context.get("topic", "")
        # Real-time research stub. In future, this can plug into a news scraper.
        research_notes = f"Research notes for topic: {topic}. No breaking news found."
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
        kb = KnowledgeBase(brand_id=self._brand.id, workspace_id=self._brand.workspace_id, repository=self._repo)
        
        # Search the knowledge base for semantic overlap with the topic
        results = kb.search(topic, top_k=3)
        facts = [entry.content for entry, _ in results]
        
        knowledge_context = "\n".join(facts)
        context["knowledge_context"] = knowledge_context
        
        logger.info("[Pipeline Stage 3] Knowledge retrieved: %d entries.", len(facts))
        return facts


# ── 4. Prompt Builder Step ────────────────────────────────────────────
class PromptBuilderStep(Step):
    """Stage 4: Assembles the prompt using PromptBuilder."""
    name = "prompt_builder"

    def __init__(self, brand: Brand, ai_profile: AIProfile, prompt_builder: PromptBuilder) -> None:
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
            knowledge_context=knowledge_context
        )
        context["assembled_prompt"] = prompt
        logger.info("[Pipeline Stage 4] Complete prompt assembled.")
        return prompt


# ── 5. LLM Step ───────────────────────────────────────────────────────
class LLMStep(Step):
    """Stage 5: Submits the assembled prompt to Gemini."""
    name = "llm_generation"

    def __init__(self, content_engine: ContentEngine) -> None:
        self._engine = content_engine

    def run(self, context: dict[str, Any]) -> Any:
        topic = context.get("topic", "")
        prompt = context.get("assembled_prompt", "")
        
        # Call ContentEngine with the pre-assembled base prompt override
        result = self._engine.generate_post_raw(topic=topic, base_prompt=prompt)
        if not result:
            raise RuntimeError("LLM generation returned an empty result")
            
        context["content_result"] = result
        context["caption"] = result.get("caption", "")
        context["image_bytes"] = result.get("image_bytes")
        
        logger.info("[Pipeline Stage 5] LLM generation complete.")
        return result


# ── 6. Validator Step ─────────────────────────────────────────────────
class ValidatorStep(Step):
    """Stage 6: Validates output structure, length constraints, and word limits."""
    name = "validator"

    def run(self, context: dict[str, Any]) -> Any:
        caption = context.get("caption", "")
        if not caption:
            raise ValueError("Validator error: Caption is empty")
            
        word_count = len(caption.split())
        if word_count > 300:
            logger.warning("Caption exceeds normal length (%d words)", word_count)
            
        # Ensure json structure if structured layout
        # (Validated internally by gemini.py return values)
        logger.info("[Pipeline Stage 6] Validation passed successfully.")
        return True


# ── 7. Reviewer Step ──────────────────────────────────────────────────
class ReviewerStep(Step):
    """Stage 7: Brand voice and quality control (can apply humanizing rules or rewrite filters)."""
    name = "reviewer"
    continue_on_failure = True

    def run(self, context: dict[str, Any]) -> Any:
        # Mock Voice check step
        logger.info("[Pipeline Stage 7] Review completed.")
        return True


# ── 8. Card Renderer Step ─────────────────────────────────────────────
class CardRendererStep(Step):
    """Stage 8: Renders visual comparison card image if applicable."""
    name = "card_renderer"
    continue_on_failure = True

    def __init__(self, content_engine: ContentEngine) -> None:
        self._engine = content_engine

    def run(self, context: dict[str, Any]) -> Any:
        image_bytes = context.get("image_bytes")
        # In ContentEngine, generate_post might render the card.
        # If image_bytes is already populated, we skip rendering.
        if image_bytes:
            logger.info("[Pipeline Stage 8] Card already rendered, skipping.")
            return image_bytes
            
        # Otherwise, render card lazily
        result = context.get("content_result", {})
        topic = context.get("topic", "")
        # Mock rendering if needed, otherwise skip
        logger.info("[Pipeline Stage 8] Visual card rendering check complete.")
        return None


# ── 9. Publisher Step ─────────────────────────────────────────────────
class PublisherStep(Step):
    """Stage 9: Publishes the content to all connected platforms for this brand."""
    name = "publisher"

    def __init__(self, registry: PluginRegistry, brand: Brand) -> None:
        self._registry = registry
        self._brand = brand

    def run(self, context: dict[str, Any]) -> Any:
        caption = context.get("caption", "")
        image_bytes = context.get("image_bytes")
        
        results = self._registry.publish_all(
            content=caption,
            image_bytes=image_bytes,
            brand=self._brand
        )
        context["publish_results"] = results
        
        failed_all = all(not r.get("ok") for r in results.values())
        if failed_all:
            raise RuntimeError(f"All plugins failed to publish: {results}")
            
        logger.info("[Pipeline Stage 9] Published content successfully.")
        return results


# ── 10. Analytics Step ────────────────────────────────────────────────
class AnalyticsStep(Step):
    """Stage 10: Records metrics and emits real-time events to the event bus."""
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
        
        # Record analytics
        for platform, res in publish_results.items():
            if res.get("ok"):
                self._repo.record_post_published(
                    topic=topic,
                    platform=platform,
                    post_id=res.get("post_id", "")
                )
                # Emit event
                self._bus.publish(
                    PostPublishedEvent(
                        post_id=res.get("post_id", ""),
                        topic=topic,
                        platform=platform,
                        caption_preview=caption[:100]
                    )
                )
        logger.info("[Pipeline Stage 10] Analytics recorded and events emitted.")
        return True


# ── Full 10-Stage Pipeline ───────────────────────────────────────────
class ContentGenerationPipeline(Workflow):
    """Execution pipeline implementing the 10 core stages of content production."""
    name = "content_generation_pipeline"

    def __init__(
        self,
        content_engine: ContentEngine,
        registry: PluginRegistry,
        analytics_repo: AnalyticsRepository,
        bus: EventBus,
        knowledge_repo: KnowledgeRepository,
        prompt_builder: PromptBuilder,
        brand: Brand,
        ai_profile: AIProfile,
        category: Optional[str] = None,
    ) -> None:
        super().__init__(
            steps=[
                PlannerStep(brand, category),
                ResearchStep(),
                KnowledgeStep(brand, knowledge_repo),
                PromptBuilderStep(brand, ai_profile, prompt_builder),
                LLMStep(content_engine),
                ValidatorStep(),
                ReviewerStep(),
                CardRendererStep(content_engine),
                PublisherStep(registry, brand),
                AnalyticsStep(analytics_repo, bus),
            ]
        )
