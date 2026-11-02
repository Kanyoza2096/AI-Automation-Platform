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

V1.16 — DIRECTION-BASED TOPIC GENERATION REMOVED:

  The direction-based generation path (brand.topic_direction read by the
  model to invent topics) was removed. Direction generation produced
  abstract topics from the brand charter that the corpus could not
  support — retrieval returned irrelevant chunks, and the prompt's
  specificity contract forced the model to invent metrics (e.g.,
  "our auth latency was 250ms until we switched to JWT").

  Topics now come exclusively from configured sources, in this order:
    1. brand.custom_topics
    2. brand_config.fallback_topics + keywords
    3. Knowledge Base entries (metadata.title grouped by metadata.theme)
    4. Platform fallback topics

  Every topic is backed by at least one record, so retrieval is grounded.
  brands.topic_direction is retained in the schema for backward
  compatibility but is no longer read.

V1.15 — AXIS PERSISTENCE MATCHES THE RIGHT COLUMN:

  automation_jobs has two identifiers: `id` (primary key, job_*) and
  `job_id` (secondary, pub_*). The workflow context carries the `job_id`
  value, but _record_job_axis filtered on `id`, matched zero rows, and
  failed silently, so topic / topic_axis were never saved and rotation had
  no memory. It now matches `job_id` first, falls back to `id`, and logs a
  WARNING when neither matches.

V1.14 — KB-DRIVEN AXIS ROTATION WIRING:

  prompt_builder V2.6 draws the rotation pool from the themes in the
  brand's knowledge base (brand.topic_axes is only a priority hint). Two
  consequences for this workflow:

    1. The last-published-axis lookup is no longer gated on
       brand.topic_axes. A brand with an empty topic_axes but a themed KB
       previously got NO last_used_axis, so rotation had no memory and
       could repeat the same theme back to back.
    2. build_post_prompt() is synchronous and may hit the database on a
       theme-cache miss. GenerateContentStep now warms the theme cache
       through the executor (aget_brand_themes) BEFORE building the prompt,
       so the build itself is a pure in-memory cache hit and never blocks
       the event loop.

V1.13 — TOPIC AXIS ROTATION, PERSISTED PER JOB:

  prompt_builder only ROTATES axes when told which axis the previous post
  used (`last_used_axis`); it keeps no rotation state of its own. This
  workflow builds the prompt itself and hands it to ContentEngine as
  `base_prompt`, so ContentEngine's own axis logic is a no-op here. The
  workflow owns the whole cycle:

    1. GenerateContentStep reads the brand's last PUBLISHED axis from
       automation_jobs and passes it to build_post_prompt(last_used_axis=).
    2. Immediately after the build (no await in between) it copies
       prompt_builder.last_used_axis into context["topic_axis"].
    3. PublishStep, on success, writes topic_axis (and topic) onto the
       automation_jobs row identified by context["job_id"] — the exact job,
       not "the latest job for the brand". If context has no job_id,
       nothing is recorded and rotation has no memory for that run.

  Requires a nullable text column automation_jobs.topic_axis.

V1.12 — Hashtag-tail false positive fixed in _caption_is_well_formed;
  PromptBuilder always used when present (not gated on knowledge_context);
  EmitEventStep publishes ("post.published", payload) to UnifiedEventBus.
V1.11 — PublishStep writes post_history (best-effort).
V1.10 — Truncation guard in GenerateContentStep.
V1.9  — PublishStep raises StepFailure instead of RuntimeError.
V1.8  — Direction-based topic generation. REMOVED in V1.16.
V1.7  — Publish result contract normalization.
V1.6  — Async-native steps.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

from workflows.base import Step, Workflow, StepFailure
from models.media_artifact import MediaArtifact, MediaStatus  # noqa: F401
from core.errors import (
    AIValidationError,
    RenderFailedError,  # noqa: F401
    KanyozaError,  # noqa: F401
)

if TYPE_CHECKING:
    from models.brand import Brand
    from models.ai_profile import AIProfile
    from services.content_engine import ContentEngine
    from services.content_validator import ContentValidator
    from plugins.base import PluginRegistry
    from core.event_bus import EventBus
    from repositories.analytics_repository import AnalyticsRepository

logger = logging.getLogger(__name__)

# Trailing hashtag block — one or more whitespace-separated tokens that
# begin with #. Stripped before the terminal-punctuation check in
# _caption_is_well_formed so that a post ending in hashtags is not
# rejected as truncated.
_TRAILING_HASHTAGS_RE = re.compile(r"(?:\s*#\w+)+$")


# ── 1. Select Topic ───────────────────────────────────────────────────
class SelectTopicStep(Step):
    """Pick a topic from the brand's configured sources.

    V1.16 — DIRECTION REMOVED:
      The direction-based generation path was removed. Topics now come
      exclusively from configured sources (custom_topics, brand_config,
      or the brand's Knowledge Base), so every topic is backed by at least
      one record and retrieval is grounded.

    Priority order (handled by TopicSelector):
      1. custom_topics
      2. brand_config.fallback_topics
      3. Knowledge Base entries (metadata.title grouped by metadata.theme)
      4. Platform fallback topics

    Brands with no configured topic source raise ValueError instead of
    publishing the "unconfigured" sentinel text.
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

        if not selector.is_configured():
            logger.error(
                "PostWorkflow: brand '%s' has no configured topic source. "
                "Add custom_topics, brand_config.fallback_topics, or KB entries.",
                self._brand.name,
            )
            raise ValueError(
                f"Brand '{self._brand.name}' has no configured topic source"
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

    Async-native. continue_on_failure=True so a missing or empty KB never
    blocks the pipeline — the content engine will still generate without
    context.
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
            await kb.initialize()
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

    - Async-native. ContentEngine.generate_post is async.
    - Rejects malformed/truncated captions before they reach
      ValidateContentStep and PublishStep (_caption_is_well_formed).
    - PromptBuilder.build_post_prompt() is always used when a builder is
      present, regardless of whether the brand has KB entries.
    - Topic axis rotation: reads the brand's last published axis, passes it
      to the builder, and captures the chosen axis into
      context["topic_axis"] for PublishStep to persist.
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

    async def _fetch_last_used_axis(self) -> str | None:
        """Last PUBLISHED axis for this brand, or None.

        V1.14 — No longer gated on brand.topic_axes: the rotation pool is
        derived from the KB, so every brand with a themed KB needs the
        previous axis to avoid back-to-back repeats.
        """
        getter = getattr(self._engine, "_get_last_used_axis", None)
        if not callable(getter):
            return None
        try:
            brand_id = self._engine._brand_id(self._brand)
            return await getter(brand_id)
        except Exception as exc:
            logger.debug("PostWorkflow: last-axis lookup failed: %s", exc)
            return None

    async def _warm_theme_cache(self) -> None:
        """Populate the KB theme cache off the event loop.

        build_post_prompt() is synchronous; on a cache miss it would run a
        blocking database query inside the loop. Warming first makes the
        build a pure in-memory read. Best-effort: failure just means the
        build does its own (fail-soft) lookup.
        """
        try:
            from services.knowledge_base import aget_brand_themes
            await aget_brand_themes(
                getattr(self._brand, "id", None),
                getattr(self._brand, "workspace_id", None),
            )
        except Exception as exc:
            logger.debug("PostWorkflow: theme cache warm-up failed: %s", exc)

    async def async_run(self, context: dict[str, Any]) -> Any:
        topic = context.get("topic", "")
        knowledge_context = context.get("knowledge_context", "")

        # Reset so a stale axis from an earlier run of this context can
        # never be persisted against a post that used no axis.
        context["topic_axis"] = None

        base_prompt: str | None = None
        if self._prompt_builder:
            try:
                resolved_platform = (
                    context.get("platform")
                    or getattr(self._brand, "platform", None)
                )
                last_used_axis = await self._fetch_last_used_axis()
                await self._warm_theme_cache()

                base_prompt = self._prompt_builder.build_post_prompt(
                    brand=self._brand,
                    ai_profile=self._ai_profile,
                    topic=topic,
                    knowledge_context=knowledge_context or None,
                    platform=resolved_platform,
                    last_used_axis=last_used_axis,
                )
                # Capture IMMEDIATELY — build_post_prompt is synchronous and
                # the builder instance is shared, so nothing may be awaited
                # between the build and this read.
                context["topic_axis"] = getattr(
                    self._prompt_builder, "last_used_axis", None
                )
                logger.info(
                    "PostWorkflow: prompt assembled via PromptBuilder "
                    "(kb_chars=%d, platform=%s, axis=%s, previous_axis=%s) "
                    "for brand='%s'",
                    len(knowledge_context or ""),
                    resolved_platform or "facebook",
                    context["topic_axis"] or "none",
                    last_used_axis or "none",
                    self._brand.name,
                )
            except Exception as exc:
                logger.warning(
                    "PostWorkflow: prompt assembly failed (%s) — "
                    "falling back to ContentEngine's internal prompt",
                    exc,
                )
                base_prompt = None
                context["topic_axis"] = None

        result = await self._engine.generate_post(
            brand=self._brand,
            ai_profile=self._ai_profile,
            topic=topic,
            base_prompt=base_prompt,
            verified_evidence=context.get("verified_evidence"),
        )
        if not result:
            raise AIValidationError(
                message=f"ContentEngine returned no result for '{topic}'",
                context={"brand": self._brand.name, "topic": topic},
            )

        supplied_caption = context.get("scheduled_caption")
        caption_to_check = (
            str(supplied_caption)
            if supplied_caption is not None
            else str(result.get("caption", ""))
        )
        if not self._caption_is_well_formed(caption_to_check):
            logger.warning(
                "PostWorkflow: rejecting malformed caption for brand='%s' "
                "topic='%s' len=%d publishable=%s degraded=%s reason=%s",
                self._brand.name,
                topic[:80],
                len(caption_to_check),
                result.get("publishable"),
                result.get("degraded"),
                result.get("degraded_reason"),
            )
            raise AIValidationError(
                message=(
                    f"Generated caption appears truncated or malformed "
                    f"(topic={topic!r}, len={len(caption_to_check)})"
                ),
                context={
                    "brand": self._brand.name,
                    "topic": topic,
                    "caption_len": len(caption_to_check),
                    "publishable": result.get("publishable"),
                    "degraded": result.get("degraded"),
                    "degraded_reason": result.get("degraded_reason"),
                    "media_status": result.get("media_status"),
                },
            )

        if result.get("publishable") is False:
            raise AIValidationError(
                message=(
                    f"ContentEngine marked result non-publishable "
                    f"(topic={topic!r}, reason={result.get('degraded_reason')})"
                ),
                context={
                    "brand": self._brand.name,
                    "topic": topic,
                    "degraded_reason": result.get("degraded_reason"),
                    "media_status": result.get("media_status"),
                },
            )

        # Re-check the exact caption that will be published. A scheduled or
        # caller-supplied caption must not bypass the factual-claim gate.
        from services.content_engine import ContentEngine
        claims_ok, claims_reason = ContentEngine._validate_factual_claims(
            caption_to_check, context.get("verified_evidence")
        )
        if not claims_ok:
            logger.warning(
                "PostWorkflow: rejecting unsupported quantitative claim "
                "for brand='%s' topic='%s' reason=%s",
                self._brand.name, topic[:80], claims_reason,
            )
            raise AIValidationError(
                message=f"Caption failed factual-claim validation: {claims_reason}",
                context={
                    "brand": self._brand.name,
                    "topic": topic,
                    "claim_validation_reason": claims_reason,
                },
            )

        context["content_result"] = result
        context["caption"] = caption_to_check
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

    @staticmethod
    def _caption_is_well_formed(caption: str) -> bool:
        """Structural integrity check for the caption.

        Policy validation (profanity, brand safety, medical claims) is
        ContentValidator's job. This is purely structural: it catches the
        case where the LLM response was truncated mid-markdown or
        mid-sentence because the provider hit its max_tokens budget.

        Trailing hashtags (appended by the publishing layer, or present in
        a scheduled caption) are stripped before the terminal-punctuation
        check so a post ending in hashtags is not rejected as truncated.
        """
        if not caption or not caption.strip():
            return False
        trimmed = caption.rstrip()
        if trimmed.count("**") % 2 != 0:
            return False
        if trimmed.count("```") % 2 != 0:
            return False

        stripped_body = _TRAILING_HASHTAGS_RE.sub("", trimmed).rstrip()
        if not stripped_body:
            # Caption was only hashtags — clearly malformed.
            return False

        tail = stripped_body[-200:]
        if stripped_body[-1].isalpha() and not any(c in tail for c in ".!?\"')]"):
            return False
        last_line = stripped_body.split("\n")[-1].strip()
        if (
            last_line.startswith("**")
            and not last_line.endswith("**")
            and len(last_line) > 4
        ):
            return False
        return True

    def run(self, context: dict[str, Any]) -> Any:
        raise RuntimeError(
            "GenerateContentStep must be executed via Workflow.execute_async()."
        )


# ── 3. Validate Content ───────────────────────────────────────────────
class ValidateContentStep(Step):
    """Validate AI-generated content before proceeding (async)."""
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

    - Raises workflows.base.StepFailure so the pipeline's structured
      failure survives to the API envelope.
    - Writes post_history on success (best-effort, never fails the publish).
    - Writes topic_axis / topic onto the automation_jobs row for
      context["job_id"] on success (best-effort, never fails the publish).
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

        provider_post_id = (
            output.get("platform_post_id")
            or output.get("post_id")
            or output.get("provider_object_id")
            or result.get("provider_object_id")
            or result.get("platform_post_id")
        )

        output_ok = bool(output.get("ok"))
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

            await self._write_post_history(
                context=context,
                caption=caption,
                workspace_id=workspace_id,
                platform=platform,
                decision_id=decision_id,
                provider_post_id=provider_post_id,
            )

            # Persist the axis (and topic) on the exact job row so the next
            # generation for this brand rotates.
            await self._record_job_axis(context=context, job_id=job_id)

            return publish_result

        if is_uncertain:
            publish_result.setdefault("reconciliation_required", True)
            context["publish_results"] = {}
            logger.warning(
                "PostWorkflow: publish uncertain for brand='%s' platform=%s err=%r",
                self._brand.name, platform, publish_result.get("error"),
            )
            return publish_result

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

    async def _record_job_axis(
        self,
        *,
        context: dict[str, Any],
        job_id: str | None,
    ) -> None:
        """Write topic_axis (and topic) onto the automation_jobs row.

        Never raises — the post is already live. A skipped or failed write
        is logged at WARNING because it means the next post may repeat the
        axis.
        """
        axis = context.get("topic_axis")
        topic = context.get("topic")
        if not (axis or topic):
            return
        if not job_id:
            logger.warning(
                "PostWorkflow: axis recording skipped — context has no "
                "job_id, rotation will have no memory for this post "
                "(brand='%s' axis=%s)",
                self._brand.name, axis or "none",
            )
            return

        update: dict[str, Any] = {}
        if axis:
            update["topic_axis"] = axis
        if topic:
            update["topic"] = topic

        try:
            from services.supabase import supabase_service
            client = supabase_service.service_client or supabase_service.client
            if client is None:
                logger.warning(
                    "PostWorkflow: axis recording skipped — supabase client "
                    "unavailable (job=%s axis=%s)", job_id, axis,
                )
                return
            loop = asyncio.get_running_loop()

            def _update_where(column: str):
                return (
                    client.table("automation_jobs")
                    .update(update)
                    .eq(column, job_id)
                    .execute()
                )

            # automation_jobs has TWO identifiers: `job_id` (pub_*, the value
            # the workflow context carries) and `id` (job_*, the primary
            # key). Match `job_id` first; fall back to `id` so callers that
            # pass the primary key still work.
            matched_column: str | None = None
            for column in ("job_id", "id"):
                response = await loop.run_in_executor(
                    None, _update_where, column
                )
                if getattr(response, "data", None):
                    matched_column = column
                    break

            if matched_column is None:
                logger.warning(
                    "PostWorkflow: axis recording matched 0 rows for "
                    "job_id=%s (brand='%s' axis=%s topic=%s) — checked "
                    "columns job_id and id; verify the job row exists",
                    job_id, self._brand.name, axis or "none", topic or "none",
                )
                return

            logger.info(
                "PostWorkflow: recorded topic_axis='%s' topic='%s' for "
                "%s=%s",
                axis or "none", topic or "none", matched_column, job_id,
            )
        except Exception as exc:
            logger.warning(
                "PostWorkflow: axis recording failed for job=%s axis=%s: %s",
                job_id, axis, exc,
            )

    async def _write_post_history(
        self,
        *,
        context: dict[str, Any],
        caption: str,
        workspace_id: str,
        platform: str,
        decision_id: str | None,
        provider_post_id: str | None,
    ) -> None:
        """Write a post_history row for the successful publish.

        Never raises. Failure is logged at WARNING with enough context
        (decision_id, provider_post_id, workspace_id) to reconcile by hand.
        """
        try:
            from services.supabase import supabase_service
        except Exception as exc:
            logger.warning(
                "PostWorkflow: post_history write skipped — supabase "
                "service import failed (brand='%s' decision=%s): %s",
                self._brand.name, decision_id, exc,
            )
            return

        if not (supabase_service and supabase_service.client):
            logger.warning(
                "PostWorkflow: post_history write skipped — supabase "
                "client unavailable (brand='%s' decision=%s)",
                self._brand.name, decision_id,
            )
            return

        now_iso = datetime.now(timezone.utc).isoformat()
        ph_id = (
            f"ph_{decision_id}"
            if decision_id
            else (
                f"ph_{provider_post_id}"
                if provider_post_id
                else f"ph_{uuid.uuid4().hex}"
            )
        )
        word_count = len((caption or "").split())
        topic = context.get("topic", "") or "Untitled post"
        category = context.get("category") or "general"

        row = {
            "id": ph_id,
            "topic": topic,
            "category": category,
            "template_type": platform or "facebook",
            "content": caption or "",
            "word_count": word_count,
            "facebook_post_id": provider_post_id,
            "published_at": now_iso,
            "created_at": now_iso,
            "brand_id": getattr(self._brand, "id", None),
            "workspace_id": workspace_id,
        }

        try:
            await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: supabase_service.client.table("post_history").insert(row).execute(),
            )
            logger.info(
                "PostWorkflow: post_history written id=%s post_id=%s "
                "brand='%s' platform=%s",
                ph_id, provider_post_id, self._brand.name, platform,
            )
        except Exception as exc:
            logger.warning(
                "PostWorkflow: post_history write failed (post still live) "
                "brand='%s' decision=%s post_id=%s err=%s",
                self._brand.name, decision_id, provider_post_id, exc,
            )

    def run(self, context: dict[str, Any]) -> Any:
        """Synchronous compatibility path for non-ASGI callers."""
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

    Publishes via the (event_name, payload) signature UnifiedEventBus
    requires. Event name and payload are derived from the
    PostPublishedEvent instance so the dataclass stays the source of truth
    for what fields the event carries.
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

            event_obj = PostPublishedEvent(
                post_id=result.get("post_id", ""),
                topic=topic,
                platform=platform,
                caption_preview=caption,
            )

            event_name = getattr(event_obj, "event_type", "post.published")
            if hasattr(event_name, "value"):
                event_name = event_name.value
            event_name = str(event_name)

            if hasattr(event_obj, "to_dict"):
                payload = event_obj.to_dict()
            else:
                payload = {
                    "post_id": result.get("post_id", ""),
                    "topic": topic,
                    "platform": platform,
                    "caption_preview": caption,
                }

            await self._bus.publish(event_name, payload, source="workflow")
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
      1. Select Topic          — from brand KB or custom_topics
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
