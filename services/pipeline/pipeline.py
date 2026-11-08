"""
10-Stage Content Generation and Publishing Pipeline — v13.0 (Async-Native)
==========================================================================

v13.0 CHANGES:
  - FULLY ASYNC-NATIVE (all steps async, asyncio.Lock, asyncio.wait_for)
  - FIXED: PromptBuilder ai_profile parameter mismatch
  - FIXED: ValidatorStep enum access bug
  - FIXED: EventBus async publish
  - FIXED: ContentEngine async generate_post
  - ADDED: UnifiedEventBus integration
  - All v12.6 features preserved
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Optional, Dict, List, Set, Tuple
from urllib.parse import quote

from workflows.base import Step, Workflow
from models.brand import Brand
from models.ai_profile import AIProfile
from models.media_artifact import MediaArtifact, MediaStatus
from services.content_engine import ContentEngine
from services.content_validator import ContentValidator, ValidationResult, ValidationStatus
from services.prompt_builder import PromptBuilder
from services.knowledge_base import KnowledgeBase
from repositories.knowledge_repository import KnowledgeRepository
from repositories.analytics_repository import AnalyticsRepository
from core.unified_bus import UnifiedEventBus
from core.errors import (
    KanyozaError,
    AIValidationError,
    RenderFailedError,
    NoSocialAccountError,
    ErrorCode,
)

logger = logging.getLogger("kanyoza.pipeline")


# ═══════════════════════════════════════════════════════════════════════════
# CONFIGURATION (Preserved)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class PipelineConfig:
    fallback_topic: str = "Industry insights for today"
    research_top_k: int = 5
    research_timeout_seconds: float = 15.0
    trend_query_timeout_seconds: float = 10.0
    knowledge_top_k: int = 3
    knowledge_timeout_seconds: float = 10.0
    prompt_builder_timeout_seconds: float = 10.0
    llm_timeout_seconds: float = 120.0
    llm_max_retries: int = 2
    llm_retry_backoff_seconds: float = 2.0
    validator_timeout_seconds: float = 15.0
    reviewer_min_caption_length: int = 50
    reviewer_max_hashtags: int = 10
    reviewer_min_hashtags: int = 1
    reviewer_fail_threshold_score: int = 40
    reviewer_warn_threshold_score: int = 70
    reviewer_toxic_words: Set[str] = field(default_factory=lambda: {
        "hate", "kill", "stupid", "idiot", "shut up",
        "dumb", "moron", "garbage", "worthless", "terrible",
    })
    reviewer_cta_keywords: Set[str] = field(default_factory=lambda: {
        "learn", "join", "click", "follow", "apply",
        "contact", "visit", "share", "comment", "read",
        "watch", "subscribe", "register", "download", "start",
    })
    publisher_max_retries: int = 2
    publisher_retry_backoff_seconds: float = 2.0
    publisher_timeout_seconds: float = 30.0
    publisher_circuit_breaker_threshold: int = 5
    analytics_timeout_seconds: float = 10.0
    enable_deduplication: bool = True
    dedup_window_hours: int = 24


# ═══════════════════════════════════════════════════════════════════════════
# TIMING UTILITIES (Async)
# ═══════════════════════════════════════════════════════════════════════════

class StageTimer:
    """Tracks per-stage latency — async-safe."""
    
    def __init__(self):
        self.stage_timings: Dict[str, float] = {}
        self.stage_started_at: Dict[str, float] = {}
        self._lock = asyncio.Lock()
    
    async def start(self, stage_name: str) -> None:
        async with self._lock:
            self.stage_started_at[stage_name] = time.monotonic()
    
    async def stop(self, stage_name: str) -> float:
        async with self._lock:
            start = self.stage_started_at.pop(stage_name, None)
            if start is None:
                return 0.0
            elapsed_ms = (time.monotonic() - start) * 1000
            self.stage_timings[stage_name] = round(elapsed_ms, 2)
            return elapsed_ms
    
    def get_all(self) -> Dict[str, float]:
        return dict(self.stage_timings)


# ═══════════════════════════════════════════════════════════════════════════
# DEDUPLICATION (Async)
# ═══════════════════════════════════════════════════════════════════════════

class ContentDeduplicator:
    """Prevents duplicate content generation."""
    
    def __init__(self, window_hours: int = 24):
        self.window_hours = window_hours
        self._recent_content: Dict[str, datetime] = {}
        self._lock = asyncio.Lock()
    
    async def is_duplicate(self, topic: str, brand_id: str) -> bool:
        async with self._lock:
            key = f"{brand_id}:{topic.lower().strip()}"
            last_generated = self._recent_content.get(key)
            if last_generated is None:
                return False
            elapsed = (datetime.now(timezone.utc) - last_generated).total_seconds()
            return elapsed < (self.window_hours * 3600)
    
    async def record(self, topic: str, brand_id: str) -> None:
        async with self._lock:
            key = f"{brand_id}:{topic.lower().strip()}"
            self._recent_content[key] = datetime.now(timezone.utc)
            cutoff = datetime.now(timezone.utc) - timedelta(hours=self.window_hours * 2)
            self._recent_content = {k: v for k, v in self._recent_content.items() if v >= cutoff}


# ═══════════════════════════════════════════════════════════════════════════
# CIRCUIT BREAKER (Async)
# ═══════════════════════════════════════════════════════════════════════════

class CircuitBreaker:
    """Prevents repeated failures."""
    
    def __init__(self, failure_threshold: int = 5, cooldown_seconds: float = 60.0):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._failure_count = 0
        self._state = "CLOSED"
        self._last_failure_time = 0.0
        self._lock = asyncio.Lock()
    
    @property
    def is_open(self) -> bool:
        if self._state == "CLOSED":
            return False
        if self._state == "OPEN":
            if time.monotonic() - self._last_failure_time >= self.cooldown_seconds:
                self._state = "HALF_OPEN"
                return False
            return True
        return False
    
    async def record_success(self) -> None:
        async with self._lock:
            self._failure_count = 0
            self._state = "CLOSED"
    
    async def record_failure(self) -> None:
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            if self._failure_count >= self.failure_threshold:
                self._state = "OPEN"
    
    @property
    def state(self) -> str:
        return self._state


# ═══════════════════════════════════════════════════════════════════════════
# 1. PLANNER STEP (Async)
# ═══════════════════════════════════════════════════════════════════════════

class PlannerStep(Step):
    name = "planner"

    def __init__(self, brand: Brand, category: Optional[str] = None, knowledge_repo: Optional[KnowledgeRepository] = None, config: PipelineConfig = None) -> None:
        self._brand = brand
        self._category = category
        self._knowledge_repo = knowledge_repo
        self._config = config or PipelineConfig()

    async def run_async(self, context: dict[str, Any]) -> Any:
        from domain.content.topic_selector import TopicSelector
        
        selector = TopicSelector(brand=self._brand, knowledge_repo=self._knowledge_repo)
        topic, cat = selector.pick(self._category)
        
        if not topic:
            topic = self._config.fallback_topic
            logger.warning("[Stage 1] No topic selected — using fallback: '%s'", topic)
        
        context["topic"] = topic
        context["category"] = cat
        context["content_type"] = getattr(self._brand, 'content_type', None) or "social_post"
        
        logger.info("[Stage 1] Topic planned: '%s' (%s)", topic, cat)
        return {"topic": topic, "category": cat}

    # Sync wrapper for backward compat
    def run(self, context: dict[str, Any]) -> Any:
        return asyncio.run(self.run_async(context))


# ═══════════════════════════════════════════════════════════════════════════
# 2. RESEARCH STEP (Async)
# ═══════════════════════════════════════════════════════════════════════════

class ResearchStep(Step):
    name = "research"
    continue_on_failure = True

    def __init__(
        self,
        brand: Optional[Brand] = None,
        knowledge_repo: Optional[KnowledgeRepository] = None,
        config: PipelineConfig = None,
    ) -> None:
        self._brand = brand
        self._knowledge_repo = knowledge_repo
        self._config = config or PipelineConfig()

    async def run_async(self, context: dict[str, Any]) -> Any:
        topic = context.get("topic", "")
        research_facts: List[str] = []
        sources_used: List[str] = []
        loop = asyncio.get_event_loop()

        try:
            from plugins.trend_intelligence.plugin import trend_plugin
            if trend_plugin and trend_plugin.supabase:
                encoded_topic = quote(topic, safe='')
                try:
                    result = await asyncio.wait_for(
                        loop.run_in_executor(
                            None,
                            lambda: trend_plugin.supabase.table("trend_posts")
                            .select("title, source, url")
                            .or_(f"title.ilike.%{topic}%,description.ilike.%{topic}%")
                            .limit(self._config.research_top_k)
                            .execute()
                        ),
                        timeout=self._config.trend_query_timeout_seconds,
                    )
                    if result and result.data:
                        for row in result.data:
                            research_facts.append(f"{row.get('title', '')} (source: {row.get('source', 'unknown')})")
                            sources_used.append(row.get("source", "trend_intelligence"))
                        logger.info("[Stage 2] TrendIntelligence research: %d facts", len(research_facts))
                    else:
                        logger.warning("[Stage 2] TrendIntelligence returned no data")
                except asyncio.TimeoutError:
                    logger.warning("[Stage 2] TrendIntelligence research timed out")
        except Exception as exc:
            logger.warning("[Stage 2] TrendIntelligence research failed: %s", str(exc)[:200])

        if not research_facts and self._brand:
            try:
                if self._knowledge_repo is None:
                    raise RuntimeError("KnowledgeRepository is unavailable for KB research")
                kb = KnowledgeBase(
                    brand_id=self._brand.id,
                    workspace_id=self._brand.workspace_id,
                    repository=self._knowledge_repo,
                )
                await kb.initialize()
                kb_results = await asyncio.wait_for(
                    kb.search(topic, 3),
                    timeout=self._config.knowledge_timeout_seconds,
                )
                for entry, _ in kb_results:
                    research_facts.append(entry.content)
                    sources_used.append("knowledge_base")
                if kb_results:
                    logger.info("[Stage 2] Knowledge Base research: %d facts", len(kb_results))
            except asyncio.TimeoutError:
                logger.warning("[Stage 2] Knowledge Base research timed out")
            except Exception as exc:
                logger.warning("[Stage 2] Knowledge Base research failed: %s", str(exc)[:200])

        if not research_facts:
            research_notes = f"No external research available for topic: {topic}. Proceeding with knowledge base grounding only."
            context["research_notes"] = research_notes
            context["research_sources"] = []
            logger.info("[Stage 2] Research not available — using KB grounding only.")
            return research_notes

        research_notes = "\n".join(research_facts)
        context["research_notes"] = research_notes
        context["research_sources"] = sources_used
        logger.info("[Stage 2] Research complete: %d facts", len(research_facts))
        return research_notes

    def run(self, context: dict[str, Any]) -> Any:
        return asyncio.run(self.run_async(context))


# ═══════════════════════════════════════════════════════════════════════════
# 3. CONTENT INTELLIGENCE STEP
# ═══════════════════════════════════════════════════════════════════════════

class ContentIntelligenceStep(Step):
    name = "content_intelligence"
    continue_on_failure = True

    def __init__(self, config: PipelineConfig = None) -> None:
        self._config = config or PipelineConfig()

    async def run_async(self, context: dict[str, Any]) -> Any:
        topic = context.get("topic", "")
        if not topic:
            return {}
        try:
            from services.content_intelligence import content_intelligence_engine
            result = await asyncio.wait_for(
                content_intelligence_engine.analyze(
                    topic,
                    context=str(context.get("brand_context") or ""),
                    max_results=self._config.research_top_k + 3,
                    fetch_top=4,
                    workspace_id=str(getattr(context.get("brand"), "workspace_id", None) or "platform"),
                ),
                timeout=self._config.research_timeout_seconds + 20,
            )
            context["content_intelligence"] = result.to_dict()
            # Feed only bounded analyst output into generation; evidence remains separately auditable.
            context["intelligence_context"] = "\n".join([
                result.summary,
                "Angles: " + "; ".join(result.angles[:6]),
                "Supported claims: " + "; ".join(result.claims[:8]),
                "Risks: " + "; ".join(result.risks[:6]),
            ])
            return result.to_dict()
        except Exception as exc:
            logger.warning("[Stage 3] Content Intelligence unavailable: %s", str(exc)[:200])
            context["content_intelligence"] = {"mode": "unavailable", "error": str(exc)[:200]}
            context["intelligence_context"] = ""
            return {"mode": "unavailable"}


# ═══════════════════════════════════════════════════════════════════════════
# 3. KNOWLEDGE STEP (Async)
# ═══════════════════════════════════════════════════════════════════════════

class KnowledgeStep(Step):
    name = "knowledge_retrieval"
    continue_on_failure = True

    def __init__(self, brand: Brand, repo: KnowledgeRepository, config: PipelineConfig = None) -> None:
        self._brand = brand
        self._repo = repo
        self._config = config or PipelineConfig()

    async def run_async(self, context: dict[str, Any]) -> Any:
        topic = context.get("topic", "")
        loop = asyncio.get_event_loop()
        kb = KnowledgeBase(brand_id=self._brand.id, workspace_id=self._brand.workspace_id, repository=self._repo)
        
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(None, kb.search, topic, self._config.knowledge_top_k),
                timeout=self._config.knowledge_timeout_seconds,
            )
            facts = []
            evidence = []
            for entry, score in result or []:
                text = str(getattr(entry, "content", "") or "").strip()
                if not text:
                    continue
                metadata = getattr(entry, "metadata", None)
                if not isinstance(metadata, dict):
                    metadata = {}
                source_id = str(
                    metadata.get("source_id") or getattr(entry, "id", "") or ""
                ).strip()
                if not source_id:
                    continue
                facts.append(text)
                evidence.append({
                    "source_id": source_id,
                    "text": text,
                    "metadata": metadata,
                    "score": score if isinstance(score, (int, float)) else None,
                })
            knowledge_context = "\n".join(facts)
            context["knowledge_context"] = knowledge_context
            context["knowledge_evidence"] = evidence
            logger.info(
                "[Stage 3] Knowledge retrieved: %d traceable entries.",
                len(evidence),
            )
            return evidence
        except asyncio.TimeoutError:
            logger.warning("[Stage 3] Knowledge retrieval timed out")
            context["knowledge_context"] = ""
            return []
        except Exception as exc:
            logger.warning("[Stage 3] Knowledge retrieval failed: %s", str(exc)[:200])
            context["knowledge_context"] = ""
            return []

    def run(self, context: dict[str, Any]) -> Any:
        return asyncio.run(self.run_async(context))


# ═══════════════════════════════════════════════════════════════════════════
# 4. PROMPT BUILDER STEP (Async — FIXED ai_profile)
# ═══════════════════════════════════════════════════════════════════════════

class PromptBuilderStep(Step):
    name = "prompt_builder"

    def __init__(self, brand: Brand, ai_profile: AIProfile, prompt_builder: PromptBuilder, config: PipelineConfig = None) -> None:
        self._brand = brand
        self._ai_profile = ai_profile
        self._pb = prompt_builder
        self._config = config or PipelineConfig()

    async def run_async(self, context: dict[str, Any]) -> Any:
        topic = context.get("topic", "")
        knowledge_context = context.get("knowledge_context", "")
        research_notes = context.get("research_notes", "")
        intelligence_context = context.get("intelligence_context", "")

        combined_context = knowledge_context or ""
        if research_notes and "No external research" not in research_notes:
            combined_context = f"{combined_context}\n\nResearch:\n{research_notes}" if combined_context else f"Research:\n{research_notes}"
        if intelligence_context:
            combined_context = f"{combined_context}\n\nContent Intelligence (analyst synthesis, verify against evidence):\n{intelligence_context}" if combined_context else intelligence_context

        # FIXED: Pass ai_profile correctly
        prompt = self._pb.build_post_prompt(
            brand=self._brand,
            ai_profile=self._ai_profile,
            topic=topic,
            knowledge_context=combined_context,
        )
        
        if not prompt or not prompt.strip():
            raise AIValidationError(message="PromptBuilder returned empty prompt", context={"topic": topic})
        
        context["assembled_prompt"] = prompt
        logger.info("[Stage 4] Complete prompt assembled (%d chars).", len(prompt))
        return prompt

    def run(self, context: dict[str, Any]) -> Any:
        return asyncio.run(self.run_async(context))


# ═══════════════════════════════════════════════════════════════════════════
# 5. LLM STEP (Async — FIXED async content_engine)
# ═══════════════════════════════════════════════════════════════════════════

class LLMStep(Step):
    name = "llm_generation"

    def __init__(self, content_engine: ContentEngine, brand: Optional[Any] = None, ai_profile: Optional[Any] = None, config: PipelineConfig = None) -> None:
        self._engine = content_engine
        self._brand = brand
        self._ai_profile = ai_profile
        self._config = config or PipelineConfig()

    async def run_async(self, context: dict[str, Any]) -> Any:
        topic = context.get("topic", "")
        assembled_prompt = context.get("assembled_prompt") or None
        loop = asyncio.get_event_loop()

        last_error = None
        for attempt in range(self._config.llm_max_retries + 1):
            try:
                # FIXED: ContentEngine.generate_post is now async
                result = await asyncio.wait_for(
                    self._engine.generate_post(
                        brand=self._brand,
                        ai_profile=self._ai_profile,
                        topic=topic,
                        base_prompt=assembled_prompt,
                    ),
                    timeout=self._config.llm_timeout_seconds,
                )
                
                if result:
                    context["content_result"] = result
                    context["caption"] = result.get("caption", "")
                    context["image_bytes"] = result.get("image_bytes")
                    context["media_artifact"] = result.get("media_artifact", MediaArtifact.no_media())
                    
                    media_status = context["media_artifact"].status.value
                    logger.info("[Stage 5] LLM generation complete — media=%s", media_status)
                    return result
                
                last_error = "Empty result"
                
            except asyncio.TimeoutError:
                last_error = f"Timeout after {self._config.llm_timeout_seconds}s"
            except Exception as exc:
                last_error = str(exc)[:200]
            
            if attempt < self._config.llm_max_retries:
                backoff = self._config.llm_retry_backoff_seconds * (2 ** attempt)
                logger.warning("[Stage 5] LLM attempt %d failed: %s — retrying in %.1fs", attempt + 1, last_error, backoff)
                await asyncio.sleep(backoff)

        raise AIValidationError(
            message=f"LLM generation failed after {self._config.llm_max_retries + 1} attempts: {last_error}",
            context={"brand": getattr(self._brand, "name", "unknown"), "topic": topic},
        )

    def run(self, context: dict[str, Any]) -> Any:
        return asyncio.run(self.run_async(context))


# ═══════════════════════════════════════════════════════════════════════════
# 6. VALIDATOR STEP (Async — FIXED enum access)
# ═══════════════════════════════════════════════════════════════════════════

class ValidatorStep(Step):
    name = "validator"

    def __init__(self, validator: Optional[ContentValidator] = None, config: PipelineConfig = None) -> None:
        self._validator = validator or ContentValidator()
        self._config = config or PipelineConfig()

    async def run_async(self, context: dict[str, Any]) -> Any:
        caption = context.get("caption", "")
        topic = context.get("topic", "")
        brand = context.get("brand")

        content = {
            "title": context.get("content_result", {}).get("title", topic),
            "caption": caption,
            "content_type": context.get("content_type", "social_post"),
            "hashtags": context.get("content_result", {}).get("hashtags", []),
            "media_required": True,
        }

        # FIXED: Validate is now async in ContentValidator v3.0
        validation_result = self._validator.validate(
            content=content,
            brand=brand,
            platform=context.get("platform", "facebook"),
            require_media=True,
        )
        if asyncio.iscoroutine(validation_result):
            validation_result = await validation_result

        context["validation_result"] = validation_result

        if validation_result.is_rejected:
            violations = getattr(validation_result, "violations", [])
            errors = [v.message for v in violations if v.severity.value == "error"]
            logger.error("[Stage 6] Validation REJECTED: %s", "; ".join(errors))
            raise AIValidationError(
                message=f"Content validation failed: {'; '.join(errors)}",
                context={"topic": topic},
            )

        # FIXED: Correct enum comparison
        if validation_result.status == ValidationStatus.APPROVED_WITH_WARNINGS:
            warnings = getattr(validation_result, "warnings_count", 0)
            logger.warning("[Stage 6] Validation passed with %d warnings", warnings)
        else:
            logger.info("[Stage 6] Validation passed.")

        return validation_result.to_dict()

    def run(self, context: dict[str, Any]) -> Any:
        return asyncio.run(self.run_async(context))


# ═══════════════════════════════════════════════════════════════════════════
# 7. REVIEWER STEP (Async — preserved)
# ═══════════════════════════════════════════════════════════════════════════

class ReviewerStep(Step):
    name = "reviewer"
    continue_on_failure = False

    def __init__(self, config: PipelineConfig = None) -> None:
        self._config = config or PipelineConfig()

    async def run_async(self, context: dict[str, Any]) -> Any:
        caption = context.get("caption", "")
        content_result = context.get("content_result", {})
        hashtags = content_result.get("hashtags", [])
        brand = context.get("brand")

        issues: List[str] = []
        warnings: List[str] = []

        if len(caption.strip()) < self._config.reviewer_min_caption_length:
            issues.append(f"Caption too short ({len(caption.strip())} chars, minimum {self._config.reviewer_min_caption_length})")

        if not hashtags:
            warnings.append("No hashtags provided")
        elif len(hashtags) > self._config.reviewer_max_hashtags:
            issues.append(f"Too many hashtags ({len(hashtags)}, maximum {self._config.reviewer_max_hashtags})")

        has_cta = any(kw in caption.lower() for kw in self._config.reviewer_cta_keywords)
        if not has_cta:
            warnings.append("No call-to-action detected")

        lower_caption = caption.lower()
        toxic_found = [word for word in self._config.reviewer_toxic_words if word in lower_caption]
        if toxic_found:
            issues.append(f"Potentially inappropriate language: {', '.join(toxic_found)}")

        quality_score = max(0, 100 - (20 * len(issues)) - (5 * len(warnings)))

        review_result = {
            "quality_score": quality_score,
            "passed": len(issues) == 0,
            "issues": issues,
            "warnings": warnings,
            "caption_length": len(caption.strip()),
            "hashtag_count": len(hashtags),
            "has_cta": has_cta,
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
        }

        context["review_result"] = review_result

        if quality_score < self._config.reviewer_fail_threshold_score:
            logger.error("[Stage 7] Review FAILED — score=%d, issues=%s", quality_score, "; ".join(issues))
            raise AIValidationError(message=f"Content review failed: score={quality_score}", context=review_result)

        if quality_score < self._config.reviewer_warn_threshold_score:
            logger.warning("[Stage 7] Review passed with low score=%d", quality_score)
        else:
            logger.info("[Stage 7] Review passed — score=%d", quality_score)

        return review_result

    def run(self, context: dict[str, Any]) -> Any:
        return asyncio.run(self.run_async(context))


# ═══════════════════════════════════════════════════════════════════════════
# 7b. INDEPENDENT SEMANTIC REVIEW (mandatory publication gate)
# ═══════════════════════════════════════════════════════════════════════════

class SemanticReviewStep(Step):
    """Require independent evidence- and brand-aware approval before rendering."""

    name = "semantic_review"
    continue_on_failure = False

    def __init__(self, semantic_reviewer=None) -> None:
        if semantic_reviewer is None:
            from services.content_semantic_reviewer import ContentSemanticReviewer
            semantic_reviewer = ContentSemanticReviewer()
        self._semantic_reviewer = semantic_reviewer

    async def run_async(self, context: dict[str, Any]) -> Any:
        brand = context.get("brand")
        result = await self._semantic_reviewer.review(
            caption=str(context.get("caption") or ""),
            topic=str(context.get("topic") or ""),
            evidence=context.get("knowledge_evidence") or [],
            brand=brand,
            platform=str(context.get("platform") or "facebook"),
        )
        context["semantic_review"] = result
        if (
            not isinstance(result, dict)
            or result.get("approved") is not True
            or result.get("status") != "approved"
        ):
            status = result.get("status", "invalid") if isinstance(result, dict) else "invalid"
            reason = result.get("reason", "semantic reviewer returned no decision") if isinstance(result, dict) else "semantic reviewer returned no decision"
            raise AIValidationError(
                message=f"Independent semantic review blocked publication (status={status}): {reason}",
                context={
                    "topic": context.get("topic"),
                    "semantic_review_status": status,
                    "semantic_review_reason": reason,
                },
            )
        return {
            "status": "approved",
            "approved": True,
            "scores": result.get("scores", {}),
            "supporting_source_ids": result.get("supporting_source_ids", []),
            "request_id": result.get("request_id"),
        }

    def run(self, context: dict[str, Any]) -> Any:
        raise RuntimeError("SemanticReviewStep must be executed via run_async().")


# ═══════════════════════════════════════════════════════════════════════════
# 8. CARD RENDERER STEP (Async — preserved)
# ═══════════════════════════════════════════════════════════════════════════

class CardRendererStep(Step):
    name = "card_renderer"
    continue_on_failure = True

    def __init__(self, content_engine: ContentEngine) -> None:
        self._engine = content_engine

    async def run_async(self, context: dict[str, Any]) -> Any:
        media_artifact: MediaArtifact = context.get("media_artifact")

        if media_artifact is None:
            media_artifact = MediaArtifact.no_media()
            context["media_artifact"] = media_artifact

        if media_artifact.is_valid:
            logger.info("[Stage 8] Media ready (%s, %dKB).", media_artifact.source, media_artifact.size_kb)
        elif media_artifact.is_text_only:
            logger.info("[Stage 8] Text-only post — no media needed.")
        elif media_artifact.is_pending:
            logger.warning("[Stage 8] Media still rendering")
        elif media_artifact.has_failed:
            logger.error("[Stage 8] Render FAILED: %s", media_artifact.error)
        else:
            logger.warning("[Stage 8] Unknown media status: %s", media_artifact.status)

        return media_artifact.to_dict()

    def run(self, context: dict[str, Any]) -> Any:
        return asyncio.run(self.run_async(context))


# ═══════════════════════════════════════════════════════════════════════════
# 9. PUBLISHER STEP (Async — circuit breaker)
# ═══════════════════════════════════════════════════════════════════════════

class PublisherStep(Step):
    name = "publisher"

    def __init__(self, brand: Brand, container, config: PipelineConfig = None) -> None:
        self._brand = brand
        self._container = container
        self._config = config or PipelineConfig()
        self._circuit_breaker = CircuitBreaker(failure_threshold=self._config.publisher_circuit_breaker_threshold)

    async def run_async(self, context: dict[str, Any]) -> Any:
        if self._circuit_breaker.is_open:
            raise RuntimeError("Publisher circuit breaker OPEN — too many recent failures")
        
        caption = context.get("caption", "")
        semantic_review = context.get("semantic_review")
        if (
            not isinstance(semantic_review, dict)
            or semantic_review.get("approved") is not True
            or semantic_review.get("status") != "approved"
        ):
            raise AIValidationError(
                message="Publisher blocked because independent semantic approval is missing or invalid",
                context={"brand_id": getattr(self._brand, "id", None)},
            )

        media_artifact: MediaArtifact = context.get("media_artifact")
        image_bytes: Optional[bytes] = None
        
        if media_artifact and media_artifact.is_valid:
            image_bytes = media_artifact.bytes if media_artifact.bytes else None
        elif media_artifact and media_artifact.has_failed:
            raise RenderFailedError(message=f"Media render failed: {media_artifact.error}", context={"brand": self._brand.name})
        elif media_artifact and media_artifact.is_pending:
            raise RenderFailedError(message="Media still rendering", context={"brand": self._brand.name})
        
        from domain.publishing.canonical_pipeline import run_canonical_publish

        workspace_id = str(getattr(self._brand, "workspace_id", "") or context.get("workspace_id", "")).strip()
        brand_id = str(getattr(self._brand, "id", "") or "").strip()
        platforms = context.get("platforms") or [context.get("platform") or "facebook"]
        if not workspace_id or not brand_id:
            raise RuntimeError("Canonical publisher requires workspace_id and brand_id")

        results: dict[str, Any] = {}
        for platform in platforms:
            platform = str(platform).strip().lower()
            if not platform:
                continue
            result = await run_canonical_publish(
                container=self._container,
                workspace_id=workspace_id,
                brand_id=brand_id,
                platform=platform,
                caption=caption,
                source="pipeline",
                brand=self._brand,
                image_bytes=image_bytes,
                durable=True,
            )
            results[platform] = result

        if not results:
            await self._circuit_breaker.record_failure()
            raise RuntimeError("Canonical publisher received no platform targets")

        failed = {k: v for k, v in results.items() if v.get("status") not in {"ok", "succeeded"} and not v.get("ok")}
        if not failed:
            await self._circuit_breaker.record_success()
        else:
            await self._circuit_breaker.record_failure()
        
        context["publish_results"] = results
        
        succeeded = sum(1 for r in results.values() if r.get("status") in {"ok", "succeeded"} or r.get("ok") is True)
        failed = len(results) - succeeded
        if failed == len(results):
            raise RuntimeError("All canonical publish targets failed or were blocked")
        logger.info("[Stage 9] Published — %d succeeded, %d failed", succeeded, failed)
        
        return results

    def run(self, context: dict[str, Any]) -> Any:
        return asyncio.run(self.run_async(context))


# ═══════════════════════════════════════════════════════════════════════════
# 10. ANALYTICS STEP (Async — FIXED EventBus)
# ═══════════════════════════════════════════════════════════════════════════

class AnalyticsStep(Step):
    name = "analytics"
    continue_on_failure = True

    def __init__(self, analytics_repo: AnalyticsRepository, bus: UnifiedEventBus, config: PipelineConfig = None) -> None:
        self._repo = analytics_repo
        self._bus = bus
        self._config = config or PipelineConfig()

    async def run_async(self, context: dict[str, Any]) -> Any:
        topic = context.get("topic", "")
        publish_results = context.get("publish_results", {})
        caption = context.get("caption", "")
        loop = asyncio.get_event_loop()
        
        published_count = 0
        failed_count = 0
        
        for platform, res in publish_results.items():
            if res.get("status") in {"ok", "succeeded"} or res.get("ok") is True:
                published_count += 1
                try:
                    await loop.run_in_executor(
                        None,
                        self._repo.record_post_published,
                        topic, platform, res.get("platform_post_id") or res.get("post_id", ""),
                    )
                    # FIXED: EventBus publish is now async
                    await self._bus.publish(
                        "post_published",
                        {
                            "post_id": res.get("post_id", ""),
                            "topic": topic,
                            "platform": platform,
                            "caption_preview": caption[:100],
                        },
                        source="pipeline",
                    )
                except Exception as exc:
                    logger.warning("[Stage 10] Analytics record failed for %s: %s", platform, str(exc)[:150])
            else:
                failed_count += 1
        
        logger.info("[Stage 10] Analytics recorded — %d published, %d failed", published_count, failed_count)
        return {"published": published_count, "failed": failed_count}

    def run(self, context: dict[str, Any]) -> Any:
        return asyncio.run(self.run_async(context))


# ═══════════════════════════════════════════════════════════════════════════
# FULL 10-STAGE PIPELINE (Async)
# ═══════════════════════════════════════════════════════════════════════════

class ContentGenerationPipeline(Workflow):
    """Execution pipeline implementing the 10 core stages — ASYNC-NATIVE."""

    name = "content_generation_pipeline"

    def __init__(
        self,
        content_engine: ContentEngine,
        analytics_repo: AnalyticsRepository,
        bus: UnifiedEventBus,
        knowledge_repo: KnowledgeRepository,
        prompt_builder: PromptBuilder,
        brand: Brand,
        ai_profile: AIProfile,
        container,
        category: Optional[str] = None,
        content_validator: Optional[ContentValidator] = None,
        semantic_reviewer=None,
        config: Optional[PipelineConfig] = None,
    ) -> None:
        self._config = config or PipelineConfig()
        self._timer = StageTimer()
        self._deduplicator = ContentDeduplicator(window_hours=self._config.dedup_window_hours)
        self._brand = brand
        self._ai_profile = ai_profile
        
        super().__init__(
            steps=[
                PlannerStep(brand, category, knowledge_repo=knowledge_repo, config=self._config),
                ResearchStep(
                    brand=brand,
                    knowledge_repo=knowledge_repo,
                    config=self._config,
                ),
                ContentIntelligenceStep(config=self._config),
                KnowledgeStep(brand, knowledge_repo, config=self._config),
                PromptBuilderStep(brand, ai_profile, prompt_builder, config=self._config),
                LLMStep(content_engine, brand=brand, ai_profile=ai_profile, config=self._config),
                ValidatorStep(validator=content_validator, config=self._config),
                ReviewerStep(config=self._config),
                SemanticReviewStep(semantic_reviewer=semantic_reviewer),
                CardRendererStep(content_engine),
                PublisherStep(brand, container, config=self._config),
                AnalyticsStep(analytics_repo, bus, config=self._config),
            ]
        )

    async def execute_async(self, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        context = context or {}
        context["brand"] = self._brand
        context["ai_profile"] = self._ai_profile
        
        topic = context.get("topic", "")
        brand_id = getattr(self._brand, "id", "unknown")
        
        if self._config.enable_deduplication and topic:
            if await self._deduplicator.is_duplicate(topic, brand_id):
                logger.warning("Pipeline: Duplicate content detected for topic '%s' — skipping", topic)
                return {"skipped": True, "reason": "duplicate_content", "topic": topic}
        
        # Execute all steps async
        result = {}
        step_results = []
        
        for step in self.steps:
            step_name = step.name
            await self._timer.start(step_name)
            
            try:
                if hasattr(step, "run_async"):
                    step_output = await step.run_async(context)
                elif hasattr(step, "run"):
                    step_output = await asyncio.get_event_loop().run_in_executor(None, step.run, context)
                else:
                    step_output = None
                
                step_results.append({"step": step_name, "ok": True, "output": step_output})
                await self._timer.stop(step_name)
                
            except Exception as exc:
                await self._timer.stop(step_name)
                step_results.append({"step": step_name, "ok": False, "error": str(exc)})
                
                if not getattr(step, "continue_on_failure", False):
                    logger.error("Pipeline failed at stage '%s': %s", step_name, exc)
                    result = {
                        "steps": step_results,
                        "status": "failed",
                        "failed_step": step_name,
                        "error": str(exc),
                        "stage_timings_ms": self._timer.get_all(),
                    }
                    return result
                else:
                    logger.warning("Pipeline stage '%s' failed (non-critical): %s", step_name, exc)
        
        if topic and not result.get("skipped"):
            await self._deduplicator.record(topic, brand_id)
        
        result["steps"] = step_results
        result["status"] = "completed"
        result["stage_timings_ms"] = self._timer.get_all()
        result["pipeline_completed_at"] = datetime.now(timezone.utc).isoformat()
        
        return result

    # Sync wrapper for backward compatibility
    def run(self, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Sync wrapper — runs async pipeline."""
        try:
            loop = asyncio.get_running_loop()
            # In async context — schedule and return future
            return asyncio.ensure_future(self.execute_async(context))
        except RuntimeError:
            # No running loop — run synchronously
            return asyncio.run(self.execute_async(context))

    # Alias for existing callers
    def execute(self, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self.run(context)


__all__ = [
    "ContentGenerationPipeline",
    "PipelineConfig",
    "PlannerStep",
    "ResearchStep",
    "ContentIntelligenceStep",
    "KnowledgeStep",
    "PromptBuilderStep",
    "LLMStep",
    "ValidatorStep",
    "ReviewerStep",
    "SemanticReviewStep",
    "CardRendererStep",
    "PublisherStep",
    "AnalyticsStep",
]