"""
Kanyoza Systems
Content Engine v15.9.1 — Brand-Aware Multimodal Content Pipeline (Async-Native)

v15.9.1 CHANGES — QUALITY GATE FIX:

  1. WORD-COUNT CAP REPLACES SENTENCE-COUNT CAP.
     v15.6's gate capped captions at 6 sentences. That made sense when the
     target length was 60-120 words (6 sentences × 15 words ≈ 90 words).
     When V3.3 raised the target to 180-200 words, the 6-sentence cap
     became incompatible: 180 words at 15 words/sentence is 12 sentences.
     The gate was rejecting captions that met the length target, which
     then surfaced as CONTENT_PIPELINE_FAILED with "too long: 8 sentences".

     The gate now enforces word count (80-250) — the same metric the
     prompt controls. A caption can be 8-12 sentences if it is 180-200
     words. Both the length target and the gate now agree.

  2. FIRST-PERSON GATE ADDED.
     V3.4 removed the brand charter (which had permitted "first-person
     plural") and made third person absolute in the prompt. But the
     quality gate never enforced that rule. On captions where the model
     drifted into "I" or "we" despite the prompt, the caption still
     published. The gate now rejects any caption containing first-person
     pronouns (I, me, my, we, our, us) before publish.

v15.9 CHANGES — TOPIC AXIS ROTATION (preserved):

  Wires prompt_builder topic axes into the pipeline.

  - _get_last_used_axis(brand_id): last axis of the most recent PUBLISHED
    job for the brand (automation_jobs.topic_axis).
  - _record_used_axis(brand_id, axis): stores the axis on the brand's most
    recent job so the next generation rotates.
  - generate_post() fetches the last axis once, passes it to every prompt
    build, and records the axis of the prompt that produced the post.
  - The axis is captured into a local variable IMMEDIATELY after each
    prompt build (no await in between). prompt_builder is a shared
    singleton, so reading prompt_builder.last_used_axis later would race
    with concurrent generations.
  - Prompt caching is bypassed for brands that define topic_axes: a cached
    prompt would freeze one axis, and a cache hit never calls the builder.
  - Requires a nullable text column automation_jobs.topic_axis.

v15.8 CHANGES (2026-10-27) — ROUTE TO 70B + HARD LENGTH CAP + FREE-FORM DEFAULT:
  1. PROSE ROUTES TO content_generation (70B), NOT chat (8B).
  2. PROSE max_tokens CAPPED AT 400.
  3. FREE-FORM ROUTING DEFAULTS TO True.

v15.7 CHANGES (2026-10-27) — HASHTAG POLICY:
  Model no longer produces hashtags. CaptionBuilder._get_hashtags returns
  brand.formatted_hashtags only.

v15.6 CHANGES (2026-10-27) — STYLE-AWARE GENERATION:
  - _prefers_free_form() helper + prose-first routing
  - CaptionBuilder.build() free-form passthrough
  - _validate_caption_quality() quality gate

v15.5 CHANGES (2026-10-24) — DETERMINISTIC CAPTION REMOVED.
v15.4 CHANGES (2026-10-22) — TRUNCATION DEFENSE.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Dict, Optional, Tuple

from models.content import TechPost, PostTheme
from models.media_artifact import MediaArtifact, MediaStatus
from utils.metrics import metrics

logger = logging.getLogger("kanyoza.services.content_engine")


# ═══════════════════════════════════════════════════════════════════════════
# THEME TAXONOMY
# ═══════════════════════════════════════════════════════════════════════════
#
VALID_THEME_VALUES = {
    # Technical
    "comparison", "tutorial", "checklist", "architecture",
    "code_tips", "ai", "security", "database", "backend",
    "frontend", "system_design", "cloud",
    # Devotional
    "scripture_reflection", "testimony",
    # Culinary
    "recipe",
    # General
    "reflection",
}

_CARD_THEMES = {
    "comparison", "tutorial", "checklist", "architecture", "code_tips",
    "security", "database", "backend", "frontend", "system_design", "cloud",
}

_AI_IMAGE_THEMES = {
    "ai",
    "scripture_reflection", "testimony",
    "recipe",
    "reflection",
}

THEME_ALIASES = {
    "compare": "comparison", "comparison_post": "comparison",
    "comparisons": "comparison", "debate": "comparison",
    "versus": "comparison", "vs": "comparison",
    "how_to": "tutorial", "how-to": "tutorial",
    "guide": "tutorial", "guides": "tutorial", "tutorials": "tutorial",
    "tips": "code_tips", "coding_tips": "code_tips",
    "coding": "code_tips", "programming": "code_tips",
    "checklists": "checklist",
    "system-design": "system_design", "systemdesign": "system_design",
    "architecture_design": "architecture",
    "artificial_intelligence": "ai", "machine_learning": "ai",
    "machine-learning": "ai", "llm": "ai", "genai": "ai",
    "cybersecurity": "security", "cyber_security": "security",
    "db": "database", "databases": "database",
    "server": "backend", "servers": "backend", "api": "backend",
    "web": "frontend", "ui": "frontend", "ux": "frontend",
    "cloud_computing": "cloud",
    "devotional": "scripture_reflection", "sermon": "scripture_reflection",
    "bible_study": "scripture_reflection", "worship": "scripture_reflection",
    "faith": "scripture_reflection", "prayer": "scripture_reflection",
    "witness": "testimony", "personal_story": "testimony",
    "cooking": "recipe", "food": "recipe", "dish": "recipe",
    "meal": "recipe", "cuisine": "recipe",
    "contemplation": "reflection", "meditation": "reflection",
    "thought": "reflection",
}


# ═══════════════════════════════════════════════════════════════════════════
# CAPTION QUALITY GATE
# ═══════════════════════════════════════════════════════════════════════════
#
_FORBIDDEN_OPENERS = (
    "as a ",
    "as an ",
    "as someone who",
    "in our experience",
    "in my experience",
    "when building",
    "when working on",
    "when developing",
    "let's take",
    "let's explore",
    "let's dive",
    "here's what you need to know",
    "the key to ",
    "the secret to ",
)

_FORBIDDEN_CLOSES = (
    "so, what's the right",
    "what do you think",
    "what's your experience",
    "have you experienced",
    "share your thoughts",
    "let us know",
    "the key takeaway",
    "the takeaway is",
    "in summary",
    "at the end of the day",
)

# Generic practitioner questions that assume an implementation history or
# production access. These are checked only against the final sentence so
# useful technical questions elsewhere in the caption remain possible.
_GENERIC_ASSUMPTIVE_CLOSE_PATTERNS = (
    re.compile(
        r"^what\\b.{0,140}\\b(?:have|has)\\s+(?:you|your team)\\s+"
        r"(?:implemented|deployed|adopted|built|configured|measured|operated)\\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"^what\\b.{0,140}\\bin your production environment\\b",
        re.IGNORECASE,
    ),
)

# v15.9.1 — first-person gate. Word-boundary regex so "I" matches
# standalone "I" but not "INTEGRATION"; "us" matches "us" but not "used".
_FIRST_PERSON_PATTERNS = (
    # Standalone first-person pronouns and possessives.
    re.compile(
        r"\b(?:i|me|my|mine|myself|we|us|our|ours|ourselves)\b",
        re.IGNORECASE,
    ),
    # First-person contractions are separate tokens and must not leak
    # through the standalone-pronoun check.
    re.compile(
        r"\b(?:i|we)(?:'m|'ve|'ll|'d|’m|’ve|’ll|’d)\b",
        re.IGNORECASE,
    ),
)
# v15.9.1 — word-count bounds. Set to accommodate the 180-200 word target
# while rejecting clear violations (tweets that pass as captions, essays).
_MIN_WORDS = 80
_MAX_WORDS = 250


class VisualStrategy(str, Enum):
    CARD = "card"
    AI_IMAGE = "ai_image"
    AUTO = "auto"


@dataclass
class ContentGenerationResult:
    post: Optional[TechPost]
    caption: str
    topic: str
    category: str
    visual_strategy: VisualStrategy
    brand_id: str
    brand_name: str


class CaptionBuilder:
    """Build a social-media-ready caption from TechPost.

    v15.6 — Free-form passthrough (no title → no template wrapper).
    v15.7 — Brand-only hashtags (model doesn't produce them anymore).
    """

    @staticmethod
    def _get_hashtags(post: TechPost, brand=None) -> str:
        """Return the brand's configured hashtags.

        v15.7 — Model hashtags are not merged in. Hashtags are a brand
        decision, not a per-post content decision.
        """
        if brand is None:
            return ""

        formatted = getattr(brand, "formatted_hashtags", None)
        if formatted:
            return str(formatted).strip()

        hashtags = getattr(brand, "hashtags", None)
        if isinstance(hashtags, (list, tuple, set)):
            return " ".join(
                f"#{str(tag).lstrip('#').strip()}"
                for tag in hashtags if str(tag).strip()
            )
        if hashtags:
            return str(hashtags).strip()
        return ""

    @staticmethod
    def build(post: TechPost, category: str, brand=None) -> str:
        title = getattr(post, "title", None)
        body = getattr(post, "caption", None)
        is_debate = bool(getattr(post, "is_debate", False))

        # v15.6 — Free-form passthrough.
        if not title and not is_debate:
            raw = str(body or "").strip()
            if not raw:
                return ""
            tags = CaptionBuilder._get_hashtags(post, brand)
            if tags and "#" in tags:
                return f"{raw}\n\n{tags}"
            return raw

        # Structured template (unchanged from v15.5)
        tags = CaptionBuilder._get_hashtags(post, brand)
        lines: list[str] = []
        if title:
            lines.append(f"🔥 {str(title).strip()}")
            lines.append("")
        if body:
            lines.append(str(body).strip())
            lines.append("")
        option_a = getattr(post, "option_a", None)
        option_b = getattr(post, "option_b", None)
        if is_debate and option_a and option_b:
            lines.append(f"⚡ {str(option_a).strip()} vs {str(option_b).strip()}:")
            points_a = getattr(post, "points_a", None) or []
            points_b = getattr(post, "points_b", None) or []
            for point in points_a[:3]:
                if point:
                    lines.append(f"  • {str(option_a).strip()}: {str(point).strip()}")
            for point in points_b[:3]:
                if point:
                    lines.append(f"  • {str(option_b).strip()}: {str(point).strip()}")
            lines.append("")
        winner = getattr(post, "winner", None)
        if winner:
            lines.append(f"🏆 Winner: {str(winner).strip()}")
            lines.append("")
        if tags:
            lines.append(tags)
        return "\n".join(lines).strip()


class ContentEngine:
    """Brand-aware multimodal content generation engine — ASYNC-NATIVE."""

    def __init__(self, container=None):
        self._container = container
        self._caption_builder = CaptionBuilder()
        self._prompt_cache: Dict[str, str] = {}
        self._redis = None
        self._event_loop = None
        self._event_bus = None

        if container:
            try:
                from core.redis_client import UpstashRedisClient
                self._redis = container.try_resolve(UpstashRedisClient)
            except Exception as exc:
                logger.debug("Redis prompt cache unavailable: %s", exc)

    # ═══════════════════════════════════════════════════════════════
    # HEALTH PROVIDER PROTOCOL
    # ═══════════════════════════════════════════════════════════════

    @property
    def service_name(self) -> str:
        return "content_engine"

    async def check_health_async(self) -> dict:
        enum_values = {t.value for t in PostTheme}
        drift = VALID_THEME_VALUES.symmetric_difference(enum_values)
        placement_drift = VALID_THEME_VALUES - (_CARD_THEMES | _AI_IMAGE_THEMES)
        return {
            "status": "healthy" if not drift and not placement_drift else "degraded",
            "version": "15.9.1",
            "prompt_cache_size": len(self._prompt_cache),
            "redis_available": self._redis is not None,
            "theme_enum_drift": sorted(drift) if drift else None,
            "themes_not_routed": sorted(placement_drift) if placement_drift else None,
            "first_person_gate": True,
            "word_count_gate": [_MIN_WORDS, _MAX_WORDS],
        }

    # ═══════════════════════════════════════════════════════════════
    # CONTAINER WIRING
    # ═══════════════════════════════════════════════════════════════

    def set_container(self, container) -> None:
        self._container = container
        try:
            from core.redis_client import UpstashRedisClient
            self._redis = container.try_resolve(UpstashRedisClient)
        except Exception:
            pass
        try:
            from core.unified_bus import UnifiedEventBus
            self._event_bus = container.try_resolve(UnifiedEventBus)
        except Exception:
            pass
        logger.info("ContentEngine: container wired")

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._event_loop = loop

    def set_event_bus(self, event_bus) -> None:
        self._event_bus = event_bus

    # ═══════════════════════════════════════════════════════════════
    # DASHBOARD TELEMETRY
    # ═══════════════════════════════════════════════════════════════

    async def _emit_dashboard_event(self, event_name: str, payload: dict) -> None:
        try:
            from core.socket_server import emit_to_dashboard
            await emit_to_dashboard(event_name, payload)
        except Exception as exc:
            logger.debug("Dashboard event '%s' failed: %s", event_name, exc)

    # ═══════════════════════════════════════════════════════════════
    # SUPABASE CLIENT
    # ═══════════════════════════════════════════════════════════════

    def _get_supabase_client(self):
        try:
            if hasattr(self, "_container") and self._container:
                from services.supabase import supabase_service
                return supabase_service.service_client or supabase_service.client
        except Exception:
            pass
        return None

    # ═══════════════════════════════════════════════════════════════
    # TOPIC AXIS PERSISTENCE
    # ═══════════════════════════════════════════════════════════════

    async def _get_last_used_axis(self, brand_id: str) -> Optional[str]:
        """Return the axis used in the most recent published post for this brand."""
        try:
            client = self._get_supabase_client()
            if client is None:
                return None
            loop = asyncio.get_event_loop()
            res = await loop.run_in_executor(
                None,
                lambda: (
                    client.table("automation_jobs")
                    .select("topic_axis")
                    .eq("brand_id", brand_id)
                    .eq("status", "PUBLISHED")
                    .not_.is_("topic_axis", "null")
                    .order("updated_at", desc=True)
                    .limit(1)
                    .execute()
                ),
            )
            if res and res.data and len(res.data) > 0:
                return res.data[0].get("topic_axis")
        except Exception as exc:
            logger.debug("_get_last_used_axis failed: %s", exc)
        return None

    async def _record_used_axis(self, brand_id: str, axis_name: Optional[str]) -> None:
        """Record the axis used for this post so the next one rotates."""
        if not axis_name:
            return
        try:
            client = self._get_supabase_client()
            if client is None:
                return
            loop = asyncio.get_event_loop()
            res = await loop.run_in_executor(
                None,
                lambda: (
                    client.table("automation_jobs")
                    .select("id")
                    .eq("brand_id", brand_id)
                    .order("created_at", desc=True)
                    .limit(1)
                    .execute()
                ),
            )
            if res and res.data and len(res.data) > 0:
                job_id = res.data[0].get("id")
                if job_id:
                    await loop.run_in_executor(
                        None,
                        lambda: (
                            client.table("automation_jobs")
                            .update({"topic_axis": axis_name})
                            .eq("id", job_id)
                            .execute()
                        ),
                    )
        except Exception as exc:
            logger.debug("_record_used_axis failed: %s", exc)

    @staticmethod
    def _axis_from_builder() -> Optional[str]:
        """Read the axis chosen by the most recent build_post_prompt call.

        MUST be called immediately after _build_prompt with no await in
        between — prompt_builder is a shared singleton.
        """
        from services.prompt_builder import prompt_builder
        return prompt_builder.last_used_axis

    # ═══════════════════════════════════════════════════════════════
    # BRAND HELPERS
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def _brand_id(brand) -> str:
        return str(getattr(brand, "id", None) or getattr(brand, "slug", None) or "default")

    @staticmethod
    def _brand_name(brand) -> str:
        return str(getattr(brand, "name", None) or getattr(brand, "brand_name", None) or "Default")

    # ═══════════════════════════════════════════════════════════════
    # FREE-FORM DETECTION (default True)
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def _prefers_free_form(brand, ai_profile=None) -> bool:
        """Return True if this brand/profile uses free-form caption style.

        v15.8 — DEFAULT IS FREE-FORM. Only an explicit opt-out via
        content_style="structured" (direct attribute or metadata) changes
        the behavior.
        """
        for source in (brand, ai_profile):
            if source is None:
                continue

            for attr in ("content_style", "caption_style", "post_style"):
                val = getattr(source, attr, None)
                if isinstance(val, str) and val.strip().lower() in (
                    "structured", "template", "debate"
                ):
                    return False

            metadata = getattr(source, "metadata", None) or {}
            if isinstance(metadata, dict):
                style = metadata.get("content_style") or metadata.get("caption_style")
                if isinstance(style, str) and style.strip().lower() in (
                    "structured", "template", "debate"
                ):
                    return False

        return True  # default

    # ═══════════════════════════════════════════════════════════════
    # PROMPT CACHE
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def _cache_key(brand, ai_profile, topic: str, *, structured: bool = False) -> str:
        brand_id = str(getattr(brand, "id", None) or getattr(brand, "slug", None) or "default")
        profile_id = str(
            getattr(ai_profile, "id", None)
            or getattr(ai_profile, "slug", None)
            or getattr(ai_profile, "name", None)
            or "default"
        )
        normalized_topic = " ".join(str(topic).lower().split())
        suffix = "structured" if structured else "prose"
        digest = hashlib.sha256(
            f"{normalized_topic}::{suffix}".encode("utf-8")
        ).hexdigest()[:24]
        return f"kanyoza:prompt:{brand_id}:{profile_id}:{suffix}:{digest}"

    def _cached_prompt(
        self, brand, ai_profile, topic: str, *, structured: bool = False,
    ) -> Optional[str]:
        key = self._cache_key(brand, ai_profile, topic, structured=structured)
        if self._redis:
            try:
                if hasattr(self._redis, "is_available") and self._redis.is_available:
                    cached = self._redis.get(key)
                    if cached:
                        return cached
            except Exception as exc:
                logger.debug("Redis prompt lookup failed: %s", exc)
        return self._prompt_cache.get(key)

    def _cache_prompt(
        self, brand, ai_profile, topic: str, prompt: str, *, structured: bool = False,
    ) -> None:
        key = self._cache_key(brand, ai_profile, topic, structured=structured)
        self._prompt_cache[key] = prompt
        if self._redis:
            try:
                if hasattr(self._redis, "is_available") and self._redis.is_available:
                    self._redis.set(key, prompt, ex=1800)
            except Exception as exc:
                logger.debug("Redis prompt cache write failed: %s", exc)

    # ═══════════════════════════════════════════════════════════════
    # AUTONOMOUS CONTROL INTERFACE
    # ═══════════════════════════════════════════════════════════════

    async def boost_content(self, brand_id: str, factor: float = 0.20, duration_posts: int = 5) -> dict:
        logger.info("ContentEngine: boost_content brand=%s factor=%.2f posts=%d", brand_id, factor, duration_posts)
        policy_update = {
            "content_mix_change": factor,
            "boost_until": (datetime.now(timezone.utc) + timedelta(hours=duration_posts * 2)).isoformat(),
            "active_policy": "boost",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        persisted = False
        try:
            client = self._get_supabase_client()
            if client:
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None,
                    lambda: client.table("brands").update(policy_update).eq("id", brand_id).execute(),
                )
                persisted = bool(result.data)
        except Exception as e:
            logger.error("ContentEngine: failed to persist boost policy: %s", e)
        await self._emit_dashboard_event("content.boost", {"brand_id": brand_id, "factor": factor, "duration_posts": duration_posts, "persisted": persisted, "timestamp": datetime.now(timezone.utc).isoformat()})
        return {"action": "boost_content", "brand_id": brand_id, "factor": factor, "duration_posts": duration_posts, "status": "persisted" if persisted else "failed_to_persist", "policy": policy_update}

    async def reduce_content(self, brand_id: str, factor: float = -0.20, duration_posts: int = 5) -> dict:
        logger.info("ContentEngine: reduce_content brand=%s factor=%.2f posts=%d", brand_id, factor, duration_posts)
        policy_update = {
            "content_mix_change": factor,
            "reduce_until": (datetime.now(timezone.utc) + timedelta(hours=duration_posts * 2)).isoformat(),
            "active_policy": "reduce",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        persisted = False
        try:
            client = self._get_supabase_client()
            if client:
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None,
                    lambda: client.table("brands").update(policy_update).eq("id", brand_id).execute(),
                )
                persisted = bool(result.data)
        except Exception as e:
            logger.error("ContentEngine: failed to persist reduce policy: %s", e)
        await self._emit_dashboard_event("content.reduce", {"brand_id": brand_id, "factor": factor, "duration_posts": duration_posts, "persisted": persisted, "timestamp": datetime.now(timezone.utc).isoformat()})
        return {"action": "reduce_content", "brand_id": brand_id, "factor": factor, "duration_posts": duration_posts, "status": "persisted" if persisted else "failed_to_persist", "policy": policy_update}

    async def safe_mode(self, brand_id: str, duration_posts: int = 5) -> dict:
        logger.warning("ContentEngine: safe_mode brand=%s posts=%d", brand_id, duration_posts)
        policy_update = {
            "content_mix_change": -0.30,
            "experimental_content_disabled": True,
            "safe_mode_until": (datetime.now(timezone.utc) + timedelta(hours=duration_posts * 2)).isoformat(),
            "active_policy": "safe_mode",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        persisted = False
        try:
            client = self._get_supabase_client()
            if client:
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None,
                    lambda: client.table("brands").update(policy_update).eq("id", brand_id).execute(),
                )
                persisted = bool(result.data)
        except Exception as e:
            logger.error("ContentEngine: failed to persist safe_mode policy: %s", e)
        await self._emit_dashboard_event("content.safe_mode", {"brand_id": brand_id, "duration_posts": duration_posts, "persisted": persisted, "timestamp": datetime.now(timezone.utc).isoformat()})
        return {"action": "safe_mode", "brand_id": brand_id, "duration_posts": duration_posts, "status": "persisted" if persisted else "failed_to_persist", "experimental_content_disabled": True, "policy": policy_update}

    # ═══════════════════════════════════════════════════════════════
    # THEME NORMALIZATION
    # ═══════════════════════════════════════════════════════════════

    @classmethod
    def _normalize_theme(cls, raw_theme: Any, *, post_data: Optional[dict] = None, topic: str = "") -> str:
        value = raw_theme
        if hasattr(value, "value"):
            value = value.value
        if value is not None:
            normalized = str(value).strip().lower().replace(" ", "_")
            normalized = THEME_ALIASES.get(normalized, normalized)
            if normalized in VALID_THEME_VALUES:
                return normalized
        data = post_data or {}
        if data.get("option_a") and data.get("option_b"):
            return "comparison"
        topic_lower = str(topic).lower()

        if any(word in topic_lower for word in ("security", "cybersecurity", "hack", "vulnerability", "breach")):
            return "security"
        if any(word in topic_lower for word in ("database", "sql", "mysql", "postgres", "supabase", "sqlite", "mongodb")):
            return "database"
        if any(word in topic_lower for word in ("frontend", "react", "vue", "angular", "css", "html")):
            return "frontend"
        if any(word in topic_lower for word in ("backend", "api", "django", "flask", "fastapi", "laravel", "express")):
            return "backend"
        if any(word in topic_lower for word in ("cloud", "aws", "azure", "gcp", "serverless", "kubernetes")):
            return "cloud"
        if any(word in topic_lower for word in ("architecture", "microservice", "microservices")):
            return "architecture"
        if any(word in topic_lower for word in ("system design", "distributed system", "distributed systems")):
            return "system_design"
        if any(word in topic_lower for word in (" ai ", "artificial intelligence", "machine learning", "gemini", "llm", "chatgpt")):
            return "ai"

        if any(word in topic_lower for word in (
            "scripture", "psalm", "proverb", "gospel", "verse", "bible",
            "faith", "grace", "prayer", "worship", "god", "jesus", "christ",
            "lord", "holy", "spirit", "salvation", "redemption", "devotional",
            "sermon",
        )):
            return "scripture_reflection"
        if any(word in topic_lower for word in (
            "testimony", "journey", "witness", "walk with", "personal story",
            "how god", "what god",
        )):
            return "testimony"

        if any(word in topic_lower for word in (
            "recipe", "ingredient", "cook", "bake", "roast", "grill",
            "meal", "dish", "cuisine", "kitchen", "flavor", "seasoning",
        )):
            return "recipe"

        if any(word in topic_lower for word in (
            "reflection", "observation", "thought on", "meditation",
            "lesson", "learning",
        )):
            return "reflection"

        if any(word in topic_lower for word in ("tutorial", "how to", "guide", "learn")):
            return "tutorial"
        if any(word in topic_lower for word in ("checklist", "steps", "things to")):
            return "checklist"
        if any(word in topic_lower for word in ("tips", "coding", "programming", "developer")):
            return "code_tips"

        return "ai"

    @classmethod
    def _theme_enum(cls, raw_theme: Any, *, post_data: Optional[dict] = None, topic: str = ""):
        normalized = cls._normalize_theme(raw_theme, post_data=post_data, topic=topic)
        try:
            return PostTheme(normalized)
        except Exception:
            return PostTheme.AI

    def _normalize_structured_payload(self, structured: Any, topic: str) -> Any:
        if structured is None:
            return None
        if hasattr(structured, "model_dump"):
            try:
                data = structured.model_dump(mode="python")
            except Exception:
                data = None
        elif isinstance(structured, dict):
            data = dict(structured)
        else:
            data = None
        if data is None:
            return structured
        data["theme"] = self._normalize_theme(data.get("theme"), post_data=data, topic=topic)
        return data

    # ═══════════════════════════════════════════════════════════════
    # VISUAL STRATEGY
    # ═══════════════════════════════════════════════════════════════

    def _resolve_visual_strategy(self, post: TechPost, brand=None) -> VisualStrategy:
        configured = None
        if brand:
            configured = (
                getattr(brand, "visual_strategy", None)
                or getattr(brand, "image_strategy", None)
                or getattr(brand, "render_strategy", None)
            )
        if configured:
            try:
                return VisualStrategy(str(configured).lower())
            except ValueError:
                pass

        theme_value = self._theme_value(post)

        if theme_value in _AI_IMAGE_THEMES:
            ai_images_enabled = getattr(brand, "ai_images_enabled", None)
            if ai_images_enabled is not False:
                return VisualStrategy.AI_IMAGE

        if theme_value in _CARD_THEMES:
            return VisualStrategy.CARD

        if getattr(post, "option_a", None) and getattr(post, "option_b", None):
            return VisualStrategy.CARD

        if brand and getattr(brand, "ai_images_enabled", None) is True:
            return VisualStrategy.AI_IMAGE

        return VisualStrategy.CARD

    # ═══════════════════════════════════════════════════════════════
    # TOPIC SELECTION
    # ═══════════════════════════════════════════════════════════════

    def _select_topic(self, brand=None, topic: Optional[str] = None) -> Tuple[str, str]:
        if topic and str(topic).strip():
            return (str(topic).strip(), "general")
        try:
            from domain.content.topic_selector import TopicSelector
            knowledge_repo = None
            if self._container:
                try:
                    from repositories.knowledge_repository import KnowledgeRepository
                    knowledge_repo = self._container.try_resolve(KnowledgeRepository)
                except Exception:
                    pass
            selector = TopicSelector(brand=brand, knowledge_repo=knowledge_repo)
            selected = selector.pick()
            if not selected:
                raise ValueError("TopicSelector returned no topic")
            selected_topic, category = selected
            if not selected_topic:
                raise ValueError("TopicSelector returned an empty topic")
            return (str(selected_topic).strip(), str(category or "general").strip())
        except Exception:
            logger.exception("Topic selection failed")
            raise

    # ═══════════════════════════════════════════════════════════════
    # PROMPT CONSTRUCTION
    # ═══════════════════════════════════════════════════════════════

    def _build_prompt(
        self,
        brand,
        ai_profile,
        topic: str,
        base_prompt: Optional[str],
        *,
        structured: bool = False,
        last_used_axis: Optional[str] = None,
    ) -> str:
        from services.prompt_builder import prompt_builder

        # Clear any axis left over from a previous call. If this call
        # returns early (explicit base_prompt, cache hit) the builder never
        # runs, and a stale axis would otherwise be recorded against this
        # post.
        prompt_builder.last_used_axis = None

        if base_prompt:
            supplied = str(base_prompt).strip()
            from services.prompt_builder import FINAL_KDO_CAPTION_CONTRACT
            if FINAL_KDO_CAPTION_CONTRACT not in supplied:
                supplied = f"{supplied}\n\n{FINAL_KDO_CAPTION_CONTRACT}"
            return supplied

        # Do not cache when the brand rotates topic axes: the cache key
        # does not include the axis, so a cached prompt would freeze one
        # axis, and a cache hit never calls the builder.
        use_cache = not getattr(brand, "topic_axes", None)
        if use_cache:
            cached = self._cached_prompt(brand, ai_profile, topic, structured=structured)
            if cached:
                return cached

        prompt = prompt_builder.build_post_prompt(
            brand, ai_profile, topic,
            structured=structured,
            platform=getattr(brand, "platform", None),
            last_used_axis=last_used_axis,
        )
        if prompt and use_cache:
            self._cache_prompt(brand, ai_profile, topic, prompt, structured=structured)
        return str(prompt or "").strip()

    # ═══════════════════════════════════════════════════════════════
    # AI GENERATION
    # ═══════════════════════════════════════════════════════════════

    def _llm_generate_text(
        self,
        prompt: str,
        *,
        temperature: float = 0.7,
        max_tokens: int = 400,
        workspace_id: str = "platform",
    ) -> tuple:
        """Returns (text|None, mode).

        v15.8 — Routes to content_generation (70B model), not chat (8B).
        v15.8 — Default max_tokens reduced from 8192 to 400.
        v15.4 — Checks finish_reason and returns (None, mode) when it
        indicates truncation.
        """
        try:
            from ai.gateway import get_llm_gateway
            result = get_llm_gateway().complete(
                prompt,
                workspace_id=workspace_id or "platform",
                capability="content_generation",
                temperature=temperature,
                max_tokens=max_tokens,
                caller="content",
            )
            finish_reason = getattr(result, "finish_reason", None)
            if result.ok and result.text:
                if finish_reason in ("length", "max_tokens", "max_output_tokens"):
                    logger.warning(
                        "LLM response truncated: finish_reason=%s model=%s "
                        "max_tokens=%d text_len=%d caller=content",
                        finish_reason,
                        getattr(result, "model", None),
                        max_tokens,
                        len(result.text),
                    )
                    return None, result.mode.value
                return result.text, result.mode.value
            return None, result.mode.value
        except Exception as exc:
            logger.debug("content_engine gateway: %s", exc)
            return None, "unavailable"

    async def _generate_structured_content(
        self, topic: str, prompt: str, *, workspace_id: str = "platform"
    ) -> Optional[TechPost]:
        """Generate structured content through the governed AI Gateway."""
        try:
            from ai.gateway import get_llm_gateway
            from services.prompt_builder import POST_SCHEMA_REQUIRED_KEYS

            result = await get_llm_gateway().complete_async_structured(
                prompt,
                workspace_id=workspace_id or "platform",
                temperature=0.2,
                max_tokens=4096,
                required_keys=list(POST_SCHEMA_REQUIRED_KEYS),
                caller="content",
            )
        except Exception as exc:
            logger.warning("Structured gateway call raised: %s", exc)
            return None

        if not result.ok:
            logger.warning(
                "Structured gateway declined: reason=%s mode=%s provider=%s topic=%r",
                result.reason,
                getattr(result.mode, "value", result.mode),
                result.provider,
                topic,
            )
            return None

        structured = result.data
        if not isinstance(structured, dict):
            logger.warning(
                "Structured gateway returned non-dict data: type=%s topic=%r",
                type(structured).__name__, topic,
            )
            return None

        missing = [
            k for k in POST_SCHEMA_REQUIRED_KEYS
            if not str(structured.get(k) or "").strip()
        ]
        if missing:
            logger.warning(
                "Structured payload missing required keys after gateway: "
                "missing=%s keys=%s topic=%r",
                missing, sorted(structured.keys()), topic,
            )
            return None

        try:
            normalized = self._normalize_structured_payload(structured, topic)
            theme_enum = self._theme_enum(
                normalized.get("theme") if isinstance(normalized, dict) else None,
                post_data=normalized if isinstance(normalized, dict) else None,
                topic=topic,
            )
            try:
                post = TechPost.from_gemini_response(normalized)
            except TypeError:
                post = TechPost.from_gemini_response(normalized, theme=theme_enum)
            if not post:
                logger.warning(
                    "TechPost.from_gemini_response returned falsy: keys=%s topic=%r",
                    sorted(structured.keys()), topic,
                )
                return None

            current_theme = getattr(post, "theme", None)
            normalized_theme = self._normalize_theme(
                current_theme,
                post_data=normalized if isinstance(normalized, dict) else None,
                topic=topic,
            )
            try:
                post.theme = self._theme_enum(
                    normalized_theme,
                    post_data=normalized if isinstance(normalized, dict) else None,
                    topic=topic,
                )
            except Exception:
                pass
            return post
        except Exception as exc:
            logger.warning(
                "Structured AI response validation failed: %s keys=%s topic=%r",
                exc,
                sorted(structured.keys()) if isinstance(structured, dict) else "n/a",
                topic,
            )
            return None

    # ═══════════════════════════════════════════════════════════════
    # CAPTION QUALITY GATE
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def _validate_caption_quality(caption: str) -> tuple[bool, str]:
        """Reject low-quality captions before publish.

        v15.9.1 — Three changes vs. v15.6:

        1. Word-count bound replaces sentence-count bound. The sentence
           cap of 6 was incompatible with the 180-200 word target: at
           15 words/sentence, 180 words is 12 sentences. The gate was
           rejecting captions that met the length target.

        2. First-person gate added. V3.4 removed the brand charter and
           made third person absolute in the prompt. But the quality
           gate never enforced that rule. Any caption containing "I",
           "me", "my", "we", "our", "us" is now rejected.

        3. Sentence splitting retained for openers/closers — the
           forbidden lists still apply to sentence starts and ends.

        Returns (ok, reason). When ok=False, the caller must route the
        generation through _failed_generation_result — NOT publish the
        caption.
        """
        if not caption or not caption.strip():
            return False, "empty caption"

        if "**" in caption or "__" in caption:
            return False, "caption contains markdown bold/italic"

        # v15.9.1 — First-person gate. Word-boundary regex so "I" matches
        # standalone "I" but not "INTEGRATION"; "us" matches "us" but not
        # "used".
        for pattern in _FIRST_PERSON_PATTERNS:
            if pattern.search(caption):
                return False, f"first_person_violation: {pattern.pattern}"

        lower = caption.strip().lower()

        for opener in _FORBIDDEN_OPENERS:
            if lower.startswith(opener):
                return False, f"forbidden opener: {opener!r}"

        for closer in _FORBIDDEN_CLOSES:
            if lower.rstrip("?.! ").endswith(closer.rstrip("?.! ")):
                return False, f"forbidden close: {closer!r}"

        # Reject generic engagement questions that presume the reader has
        # implemented or operated the system. Only inspect the final sentence.
        final_sentence = re.split(r"(?<=[.!?])\\s+", caption.strip())[-1].strip()
        for pattern in _GENERIC_ASSUMPTIVE_CLOSE_PATTERNS:
            if pattern.search(final_sentence):
                return False, f"assumptive_generic_close: {pattern.pattern}"

        # v15.9.1 — Word-count bound replaces the sentence-count bound.
        words = caption.split()
        if len(words) > _MAX_WORDS:
            return False, f"too long: {len(words)} words (max {_MAX_WORDS})"
        if len(words) < _MIN_WORDS:
            return False, f"too short: {len(words)} words (min {_MIN_WORDS})"

        return True, "ok"

    async def generate_post(
        self,
        brand=None,
        ai_profile=None,
        topic: Optional[str] = None,
        base_prompt: Optional[str] = None,
    ) -> dict:
        started = time.monotonic()
        brand_id = self._brand_id(brand)
        brand_name = self._brand_name(brand)
        selected_topic = str(topic or "").strip()
        category = "general"

        try:
            selected_topic, category = self._select_topic(brand=brand, topic=topic)
            logger.info(
                "Pipeline start: brand='%s' topic='%s' category='%s'",
                brand_name, selected_topic[:100], category,
            )
            await self._emit_dashboard_event("ai_decision", {
                "stage": "topic_selected",
                "topic": selected_topic,
                "category": category,
                "brand_id": brand_id,
                "brand_name": brand_name,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            last_used_axis = await self._get_last_used_axis(brand_id)
            axis_used: Optional[str] = None

            if self._prefers_free_form(brand, ai_profile):
                logger.info(
                    "Pipeline: '%s' opts into free-form caption — prose-first",
                    brand_name,
                )
                prompt_prose = self._build_prompt(
                    brand=brand,
                    ai_profile=ai_profile,
                    topic=selected_topic,
                    base_prompt=base_prompt,
                    structured=False,
                    last_used_axis=last_used_axis,
                )
                axis_used = self._axis_from_builder()
                if prompt_prose:
                    text_body, intel_mode = self._llm_generate_text(
                        prompt_prose,
                        workspace_id=str(brand_id or "platform"),
                    )
                    if text_body:
                        result = await self._handle_unstructured_content(
                            caption=str(text_body),
                            topic=selected_topic,
                            category=category,
                            brand=brand,
                            brand_id=brand_id,
                            brand_name=brand_name,
                            started=started,
                        )
                        if isinstance(result, dict) and result.get("publishable"):
                            await self._record_used_axis(brand_id, axis_used)
                        if isinstance(result, dict):
                            result["intelligence_mode"] = intel_mode
                            result["degraded"] = (
                                intel_mode != "full" or result.get("degraded", False)
                            )
                        return result
                    logger.warning(
                        "Prose-first path failed for '%s' — falling back to structured",
                        brand_name,
                    )

            prompt_structured = self._build_prompt(
                brand=brand,
                ai_profile=ai_profile,
                topic=selected_topic,
                base_prompt=base_prompt,
                structured=True,
                last_used_axis=last_used_axis,
            )
            axis_used = self._axis_from_builder()
            if not prompt_structured:
                raise ValueError("Prompt construction returned an empty prompt")

            await self._emit_dashboard_event("ai_decision", {
                "stage": "content_generation_started",
                "topic": selected_topic,
                "category": category,
                "brand_id": brand_id,
                "brand_name": brand_name,
                "path": "structured_json",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            post = await self._generate_structured_content(
                topic=selected_topic,
                prompt=prompt_structured,
                workspace_id=str(brand_id or "platform"),
            )

            if post is None:
                logger.warning(
                    "Structured generation failed for topic='%s' — trying prose gateway",
                    selected_topic,
                )
                prompt_prose = self._build_prompt(
                    brand=brand,
                    ai_profile=ai_profile,
                    topic=selected_topic,
                    base_prompt=base_prompt,
                    structured=False,
                    last_used_axis=last_used_axis,
                )
                axis_used = self._axis_from_builder()
                text_body, intel_mode = self._llm_generate_text(
                    prompt_prose, workspace_id=str(brand_id or "platform"),
                )
                if text_body:
                    result = await self._handle_unstructured_content(
                        caption=str(text_body),
                        topic=selected_topic,
                        category=category,
                        brand=brand,
                        brand_id=brand_id,
                        brand_name=brand_name,
                        started=started,
                    )
                    if isinstance(result, dict) and result.get("publishable"):
                        await self._record_used_axis(brand_id, axis_used)
                    if isinstance(result, dict):
                        result["intelligence_mode"] = intel_mode
                        result["degraded"] = (
                            intel_mode != "full" or result.get("degraded", False)
                        )
                    return result

                logger.error(
                    "Content engine FAILED: no usable content from either "
                    "gateway path for topic='%s' brand='%s' mode='%s'",
                    selected_topic[:80], brand_name, intel_mode,
                )
                return self._failed_generation_result(
                    topic=selected_topic,
                    category=category,
                    brand_id=brand_id,
                    brand_name=brand_name,
                    started=started,
                    reason="no_usable_content_from_gateway",
                )

            # ── Structured path succeeded ──
            caption = self._caption_builder.build(post, category, brand=brand)
            if not caption:
                caption = (
                    getattr(post, "caption", None)
                    or getattr(post, "title", None)
                    or selected_topic
                )
            caption = str(caption).strip()

            ok, reason = self._validate_caption_quality(caption)
            if not ok:
                logger.warning(
                    "Structured caption rejected by quality gate: reason=%s "
                    "brand='%s' topic='%s'",
                    reason, brand_name, selected_topic[:80],
                )
                return self._failed_generation_result(
                    topic=selected_topic,
                    category=category,
                    brand_id=brand_id,
                    brand_name=brand_name,
                    started=started,
                    reason=f"caption_quality: {reason}",
                    caption=caption,
                )

            await self._record_used_axis(brand_id, axis_used)

            strategy = self._resolve_visual_strategy(post, brand=brand)
            logger.info(
                "Visual strategy selected: strategy='%s' theme='%s' brand='%s'",
                strategy.value, self._theme_value(post), brand_name,
            )
            await self._emit_dashboard_event("ai_decision", {
                "stage": "visual_strategy_selected",
                "strategy": strategy.value,
                "theme": self._theme_value(post),
                "topic": selected_topic,
                "brand_id": brand_id,
                "brand_name": brand_name,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            media_artifact = await self._render_image(
                post=post, topic=selected_topic, brand=brand, strategy=strategy,
            )

            elapsed_ms = (time.monotonic() - started) * 1000
            publishable = bool(media_artifact and media_artifact.is_publishable)
            if media_artifact and media_artifact.status == MediaStatus.RENDER_FAILED:
                publishable = False
            metrics.record_ai("content_engine", "generate_post", elapsed_ms, publishable)

            media_status = (
                media_artifact.status.value
                if media_artifact else MediaStatus.RENDER_FAILED.value
            )
            await self._emit_dashboard_event("ai_decision", {
                "stage": "pipeline_complete" if publishable else "render_failed",
                "topic": selected_topic,
                "category": category,
                "theme": self._theme_value(post),
                "visual_strategy": strategy.value,
                "media_status": media_status,
                "artifact_id": media_artifact.artifact_id if media_artifact else None,
                "brand_id": brand_id,
                "brand_name": brand_name,
                "duration_ms": round(elapsed_ms, 2),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            return {
                "caption": caption,
                "image_bytes": (
                    media_artifact.bytes
                    if (media_artifact and media_artifact.has_bytes) else None
                ),
                "media_artifact": media_artifact,
                "topic": selected_topic,
                "category": category,
                "theme": self._theme_value(post),
                "visual_strategy": strategy.value,
                "publishable": publishable,
                "degraded": not publishable,
                "media_status": media_status,
            }

        except Exception as exc:
            elapsed_ms = (time.monotonic() - started) * 1000
            logger.error(
                "Post generation crashed: brand='%s' error=%s",
                brand_name, exc, exc_info=True,
            )
            metrics.record_ai("content_engine", "generate_post", elapsed_ms, False)
            return self._failed_generation_result(
                topic=selected_topic,
                category=category,
                brand_id=brand_id,
                brand_name=brand_name,
                started=started,
                reason=str(exc),
            )

    # ═══════════════════════════════════════════════════════════════
    # UNSTRUCTURED CONTENT
    # ═══════════════════════════════════════════════════════════════

    async def _handle_unstructured_content(
        self,
        caption: str,
        topic: str,
        category: str,
        brand,
        brand_id: str,
        brand_name: str,
        started: float,
    ) -> dict:
        ok, reason = self._validate_caption_quality(caption)
        if not ok:
            logger.warning(
                "Caption rejected by quality gate: reason=%s brand='%s' topic='%s'",
                reason, brand_name, topic[:80],
            )
            return self._failed_generation_result(
                topic=topic,
                category=category,
                brand_id=brand_id,
                brand_name=brand_name,
                started=started,
                reason=f"caption_quality: {reason}",
                caption=caption,
            )

        try:
            image_bytes = await self._generate_ai_image(
                topic=topic, caption=caption, brand=brand,
            )
            if image_bytes:
                artifact = MediaArtifact.ready(
                    image_bytes=image_bytes,
                    content_type="image/png",
                    source="image_engine",
                    topic=topic,
                    brand_id=brand_id,
                )
                return {
                    "caption": caption,
                    "image_bytes": image_bytes,
                    "media_artifact": artifact,
                    "topic": topic,
                    "category": category,
                    "theme": "ai",
                    "visual_strategy": VisualStrategy.AI_IMAGE.value,
                    "publishable": artifact.is_publishable,
                    "degraded": False,
                    "media_status": artifact.status.value,
                }
        except Exception as exc:
            logger.warning("Unstructured-content visual generation failed: %s", exc)
        return self._failed_generation_result(
            topic=topic,
            category=category,
            brand_id=brand_id,
            brand_name=brand_name,
            started=started,
            reason="Unstructured AI content could not be rendered",
            caption=caption,
        )

    # ═══════════════════════════════════════════════════════════════
    # RENDERING
    # ═══════════════════════════════════════════════════════════════

    async def _render_image(
        self,
        post: TechPost,
        topic: str,
        brand=None,
        strategy: VisualStrategy = VisualStrategy.AUTO,
    ) -> MediaArtifact:
        brand_id = self._brand_id(brand)
        brand_name = self._brand_name(brand)
        caption = CaptionBuilder.build(post, self._theme_value(post), brand=brand)

        if strategy == VisualStrategy.AUTO:
            strategy = self._resolve_visual_strategy(post, brand=brand)

        if strategy == VisualStrategy.AI_IMAGE:
            try:
                image_bytes = await self._generate_ai_image(
                    topic=topic, caption=caption, brand=brand, post=post,
                )
                if image_bytes:
                    return MediaArtifact.ready(
                        image_bytes=image_bytes,
                        content_type="image/png",
                        source="image_engine",
                        topic=topic,
                        brand_id=brand_id,
                    )
            except Exception as exc:
                logger.warning("AI image strategy failed: brand='%s' error=%s", brand_name, exc)

        try:
            from services.card_renderer import card_renderer
            loop = asyncio.get_event_loop()
            image_bytes = await loop.run_in_executor(
                None, card_renderer.render, post, brand,
            )
            if image_bytes:
                return MediaArtifact.ready(
                    image_bytes=image_bytes,
                    content_type="image/png",
                    source="pillow",
                    topic=topic,
                    brand_id=brand_id,
                )
        except Exception as exc:
            logger.warning("CardRenderer failed: brand='%s' error=%s", brand_name, exc)

        try:
            from core.render_queue import get_render_queue, RenderTask
            from services.card_renderer import card_renderer
            post_dict = post.model_dump(mode="json") if hasattr(post, "model_dump") else {}
            post_dict.update({
                "brand_name": brand_name,
                "brand_hashtags": getattr(brand, "formatted_hashtags", "") if brand else "",
                "brand_colors": (getattr(brand, "colors", None) or {}) if brand else {},
                "brand_metadata": (getattr(brand, "metadata", None) or {}) if brand else {},
                "visual_strategy": strategy.value,
            })
            html = ""
            try:
                html = card_renderer.render_html_only(post, brand=brand) or ""
            except Exception:
                pass
            task = RenderTask(
                brand_id=brand_id, caption=caption, html=html,
                topic=topic, post_dict=post_dict,
            )
            queue = get_render_queue()
            task_id = queue.enqueue_render(task)
            result = queue.wait_for_render(task_id, timeout=60)
            if result:
                image_bytes = getattr(result, "image_bytes", None)
                if image_bytes and len(image_bytes) > 0:
                    return MediaArtifact.ready(
                        image_bytes=image_bytes,
                        content_type=getattr(result, "content_type", None) or "image/png",
                        width=getattr(result, "width", None) or 1080,
                        height=getattr(result, "height", None) or 1080,
                        source="render_queue",
                        topic=topic,
                        brand_id=brand_id,
                        task_id=str(task_id),
                        render_time_ms=getattr(result, "render_time_ms", None),
                    )
                image_url = getattr(result, "image_url", None) or getattr(result, "url", None)
                if image_url:
                    return MediaArtifact.ready(
                        media_url=str(image_url),
                        content_type=getattr(result, "content_type", None) or "image/png",
                        width=getattr(result, "width", None) or 1080,
                        height=getattr(result, "height", None) or 1080,
                        source="render_queue",
                        topic=topic,
                        brand_id=brand_id,
                        task_id=str(task_id),
                        render_time_ms=getattr(result, "render_time_ms", None),
                    )
        except Exception as exc:
            logger.warning("RenderQueue failed: brand='%s' error=%s", brand_name, exc)

        try:
            image_bytes = await self._generate_ai_image(
                topic=topic, caption=caption, brand=brand, post=post,
            )
            if image_bytes:
                return MediaArtifact.ready(
                    image_bytes=image_bytes,
                    content_type="image/png",
                    source="image_engine",
                    topic=topic,
                    brand_id=brand_id,
                )
        except Exception as exc:
            logger.warning("ImageEngine fallback failed: brand='%s' error=%s", brand_name, exc)

        logger.error(
            "RENDER_FAILED: all visual strategies exhausted. brand='%s' topic='%s'",
            brand_name, topic[:60],
        )
        return MediaArtifact.failed(
            error="All rendering methods exhausted: CardRenderer, RenderQueue, ImageEngine",
            error_code="RENDER_FAILED",
            source=str(strategy.value),
            topic=topic,
            brand_id=brand_id,
        )

    # ═══════════════════════════════════════════════════════════════
    # AI IMAGE ENGINE
    # ═══════════════════════════════════════════════════════════════

    async def _generate_ai_image(
        self, topic: str, caption: str, brand=None, post: Optional[TechPost] = None,
    ) -> Optional[bytes]:
        from services.image_engine import generate_best_image
        theme = self._theme_value(post) if post else "ai"
        brand_name = self._brand_name(brand)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, generate_best_image, topic,
            f"{caption[:500]} | Brand: {brand_name}", theme,
        )

    # ═══════════════════════════════════════════════════════════════
    # FAILURE RESULT
    # ═══════════════════════════════════════════════════════════════

    def _failed_generation_result(
        self,
        topic: str,
        category: str,
        brand_id: str,
        brand_name: str,
        started: float,
        reason: str,
        caption: str = "",
    ) -> dict:
        elapsed_ms = (time.monotonic() - started) * 1000
        logger.error(
            "CONTENT_PIPELINE_FAILED: brand='%s' topic='%s' reason=%s",
            brand_name, topic[:80], reason,
        )
        artifact = MediaArtifact.failed(
            error=reason,
            error_code="CONTENT_PIPELINE_FAILED",
            source="content_engine",
            topic=topic,
            brand_id=brand_id,
            render_time_ms=elapsed_ms,
        )
        return {
            "caption": caption,
            "image_bytes": None,
            "media_artifact": artifact,
            "topic": topic,
            "category": category,
            "theme": None,
            "visual_strategy": None,
            "publishable": False,
            "degraded": True,
            "media_status": artifact.status.value,
            "error_code": "CONTENT_PIPELINE_FAILED",
            "error": reason,
        }

    # ═══════════════════════════════════════════════════════════════
    # FAILURE RESULT — RETIRED FALLBACK
    # ═══════════════════════════════════════════════════════════════

    def get_fallback_post(self, topic: str = "", category: str = "", brand_id: str = "default") -> dict:
        """Retired in v15.3. See docstring in the source tree."""
        raise RuntimeError(
            "get_fallback_post was removed in v15.3. Generation failures must "
            "surface to the caller for DeadLetterQueue capture, not be masked "
            "by hardcoded content. See services/scheduler/brand_scheduler.py "
            "for the retry path."
        )

    # ═══════════════════════════════════════════════════════════════
    # LEGACY API
    # ═══════════════════════════════════════════════════════════════

    async def generate_post_legacy(self, topic: Optional[str] = None) -> dict:
        try:
            from models.brand import Brand
            from models.ai_profile import AIProfile
            default_brand = Brand.default()
            default_profile = AIProfile.default()
            return await self.generate_post(
                brand=default_brand, ai_profile=default_profile, topic=topic,
            )
        except Exception as exc:
            logger.warning("Default brand/profile initialization failed: %s", exc)
            raise RuntimeError(
                f"Legacy post generation failed for topic={topic!r}: {exc}"
            ) from exc

    # ═══════════════════════════════════════════════════════════════
    # HELPERS
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def _theme_value(post: Optional[TechPost]) -> str:
        if not post:
            return ""
        theme = getattr(post, "theme", None)
        if hasattr(theme, "value"):
            return str(theme.value)
        return str(theme or "").strip()


content_engine = ContentEngine(container=None)


__all__ = [
    "ContentEngine",
    "ContentGenerationResult",
    "CaptionBuilder",
    "VisualStrategy",
    "content_engine",
]
