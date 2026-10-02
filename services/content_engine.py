"""
Kanyoza Systems
Content Engine v15.2 — Brand-Aware Multimodal Content Pipeline (Async-Native)

v15.2 CHANGES (2026-10-02):
  - _resolve_visual_strategy routes theme='ai' posts to AI_IMAGE by default.
    Previously theme='ai' fell through to CARD because 'ai' is not in
    card_themes and brands have no ai_images_enabled column to opt in.
    The result was that AI-themed posts were rendered with the branded
    card template instead of a generated image. The new rule:
      - If the brand has an explicit visual_strategy override, it wins.
      - If theme == 'ai' AND the brand has not set ai_images_enabled=False,
        route to AI_IMAGE.
      - Otherwise, fall through to the existing theme-based routing.
    The 'ai_images_enabled=False' escape hatch lets a future brand opt out
    without requiring a schema change.

v15.1 (2026-10-01) (preserved):
  - Structured generation now routes through gateway.complete_async_structured()
    with required_keys enforcement.
  - Prompt construction split into structured and prose variants.
  - _theme_enum falls back to PostTheme.AI on unrecognized values.

v15.0 (preserved):
  - FULLY ASYNC-NATIVE (async generate_post, async rendering)
  - UnifiedEventBus integration
  - HealthProvider protocol support
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Dict, Optional, Tuple

from models.content import TechPost, PostTheme
from models.media_artifact import MediaArtifact, MediaStatus
from utils.metrics import metrics

logger = logging.getLogger("kanyoza.services.content_engine")

FALLBACK_POSTS = [
    "Stay tuned for more content coming soon! 🙏",
    "We're preparing something special for you. Check back shortly! ✨",
    "New content is being crafted. Follow us for updates! 🔥",
]

VALID_THEME_VALUES = {
    "comparison", "tutorial", "checklist", "architecture",
    "code_tips", "ai", "security", "database", "backend",
    "frontend", "system_design", "cloud",
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
    "machine-learning": "ai",
    "cybersecurity": "security", "cyber_security": "security",
    "db": "database", "databases": "database",
    "server": "backend", "servers": "backend",
    "web": "frontend", "ui": "frontend", "ux": "frontend",
    "cloud_computing": "cloud",
}


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
    """Build a social-media-ready caption from TechPost."""

    @staticmethod
    def _get_hashtags(post: TechPost, brand=None) -> str:
        if brand:
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
        formatted = getattr(post, "formatted_hashtags", None)
        if formatted:
            return str(formatted).strip()
        hashtags = getattr(post, "hashtags", None)
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
        tags = CaptionBuilder._get_hashtags(post, brand)
        lines: list[str] = []
        title = getattr(post, "title", None)
        if title:
            lines.append(f"🔥 {str(title).strip()}")
            lines.append("")
        body = getattr(post, "caption", None)
        if body:
            lines.append(str(body).strip())
            lines.append("")
        is_debate = bool(getattr(post, "is_debate", False))
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
        return {
            "status": "healthy",
            "version": "15.2.0",
            "prompt_cache_size": len(self._prompt_cache),
            "redis_available": self._redis is not None,
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
    # BRAND HELPERS
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def _brand_id(brand) -> str:
        return str(getattr(brand, "id", None) or getattr(brand, "slug", None) or "default")

    @staticmethod
    def _brand_name(brand) -> str:
        return str(getattr(brand, "name", None) or getattr(brand, "brand_name", None) or "Default")

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
        if any(word in topic_lower for word in ("security", "cybersecurity", "hack", "vulnerability")):
            return "security"
        if any(word in topic_lower for word in ("database", "sql", "mysql", "postgres", "supabase")):
            return "database"
        if any(word in topic_lower for word in ("frontend", "react", "vue", "angular", "css", "html")):
            return "frontend"
        if any(word in topic_lower for word in ("backend", "api", "django", "flask", "fastapi", "laravel")):
            return "backend"
        if any(word in topic_lower for word in ("cloud", "aws", "azure", "gcp", "serverless")):
            return "cloud"
        if any(word in topic_lower for word in ("architecture", "microservice", "microservices")):
            return "architecture"
        if any(word in topic_lower for word in ("system design", "distributed system", "distributed systems")):
            return "system_design"
        if any(word in topic_lower for word in ("ai", "artificial intelligence", "machine learning", "gemini", "llm")):
            return "ai"
        if any(word in topic_lower for word in ("tutorial", "how to", "guide", "learn")):
            return "tutorial"
        if any(word in topic_lower for word in ("checklist", "steps", "things to")):
            return "checklist"
        if any(word in topic_lower for word in ("tips", "coding", "programming", "developer")):
            return "code_tips"
        return "tutorial"

    @classmethod
    def _theme_enum(cls, raw_theme: Any, *, post_data: Optional[dict] = None, topic: str = ""):
        """Return a PostTheme. Falls back to PostTheme.AI on unrecognized values."""
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
        """Decide how the image for this post is produced.

        Resolution order:
          1. Explicit brand override (visual_strategy / image_strategy /
             render_strategy), if any of those attributes exist and are set.
          2. theme == 'ai': route to AI_IMAGE unless the brand has
             explicitly opted out via ai_images_enabled=False.
          3. Theme in card_themes: CARD.
          4. Post is a debate (option_a + option_b): CARD.
          5. brand.ai_images_enabled is True: AI_IMAGE.
          6. Default: CARD.

        The v15.2 change adds step 2. Previously 'ai' was not in
        card_themes and fell through to step 6 (CARD), so AI-themed
        posts rendered with the branded card template instead of a
        generated image. The brand table has no ai_images_enabled
        column, so step 5 never fired. The new default treats 'ai'
        theme as intrinsically suited to generated imagery.
        """
        # 1. Explicit brand override wins.
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

        # 2. 'ai' theme: default to AI image unless explicitly opted out.
        if theme_value == "ai":
            ai_images_enabled = getattr(brand, "ai_images_enabled", None)
            # None means "not configured" (default on).
            # False means the brand has explicitly opted out.
            if ai_images_enabled is not False:
                return VisualStrategy.AI_IMAGE

        # 3. Card-favouring themes.
        card_themes = {
            "comparison", "tutorial", "checklist", "architecture", "code_tips",
            "security", "database", "backend", "frontend", "system_design", "cloud",
        }
        if theme_value in card_themes:
            return VisualStrategy.CARD

        # 4. Debates are always cards (structured layout required).
        if getattr(post, "option_a", None) and getattr(post, "option_b", None):
            return VisualStrategy.CARD

        # 5. Other themes: AI image if the brand opted in.
        if brand and getattr(brand, "ai_images_enabled", None) is True:
            return VisualStrategy.AI_IMAGE

        # 6. Default.
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
    ) -> str:
        """Build a post-generation prompt.

        structured=True appends the JSON schema block. structured=False omits
        it. Callers must not mix: the schema block in a prose prompt causes
        the model to emit JSON into the caption field.
        """
        if base_prompt:
            return str(base_prompt).strip()
        cached = self._cached_prompt(brand, ai_profile, topic, structured=structured)
        if cached:
            return cached
        from services.prompt_builder import prompt_builder
        prompt = prompt_builder.build_post_prompt(
            brand, ai_profile, topic, structured=structured,
        )
        if prompt:
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
        max_tokens: int = 4096,
        workspace_id: str = "platform",
    ) -> tuple:
        """Returns (text|None, intelligence_mode). AI Gateway is the sole provider boundary."""
        try:
            from ai.gateway import get_llm_gateway
            result = get_llm_gateway().complete(
                prompt,
                workspace_id=workspace_id or "platform",
                capability="chat",
                temperature=temperature,
                max_tokens=max_tokens,
                caller="content",
            )
            if result.ok and result.text:
                return result.text, result.mode.value
            return None, result.mode.value
        except Exception as exc:
            logger.debug("content_engine gateway: %s", exc)
            return None, "unavailable"

    def _deterministic_caption(self, topic: str, brand_name: str = "Default") -> str:
        """Rule-based caption when LLM is unavailable — not fake AI."""
        topic = (topic or "today's update").strip()
        return (
            f"{topic}\n\n"
            f"We're sharing a practical note for our community. "
            f"More insights from {brand_name} soon."
        )

    async def _generate_structured_content(
        self, topic: str, prompt: str, *, workspace_id: str = "platform"
    ) -> Optional[TechPost]:
        """Generate structured content through the governed AI Gateway.

        Uses gateway.complete_async_structured() — which invokes
        complete_structured() — so POST_SCHEMA_REQUIRED_KEYS is enforced at
        the gateway boundary. A missing required key or unparseable payload
        returns a DEGRADED result and this method returns None, letting
        generate_post() fall through to the prose path.

        TechPost.title and .caption are Field(..., min_length=1); the parser
        raises ValueError before construction if either is missing. No
        post-construction emptiness check is needed.
        """
        try:
            from ai.gateway import get_llm_gateway
            from services.prompt_builder import POST_SCHEMA_REQUIRED_KEYS

            result = await get_llm_gateway().complete_async_structured(
                prompt,
                workspace_id=workspace_id or "platform",
                temperature=0.2,
                max_tokens=2048,
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

        # Gateway already checked required_keys; guard against empty strings
        # (which would fail Pydantic Field(min_length=1) inside TechPost).
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

            # Existing theme normalization pass (post-construction).
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

            # ── Structured prompt for the primary JSON path ──
            prompt_structured = self._build_prompt(
                brand=brand,
                ai_profile=ai_profile,
                topic=selected_topic,
                base_prompt=base_prompt,
                structured=True,
            )
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
                    "Structured generation failed for topic='%s' — trying prose gateway then deterministic path",
                    selected_topic,
                )
                # ── Prose prompt for fallback — schema block excluded ──
                prompt_prose = self._build_prompt(
                    brand=brand,
                    ai_profile=ai_profile,
                    topic=selected_topic,
                    base_prompt=base_prompt,
                    structured=False,
                )
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
                    if isinstance(result, dict):
                        result["intelligence_mode"] = intel_mode
                        result["degraded"] = (
                            intel_mode != "full" or result.get("degraded", False)
                        )
                    return result

                det = self._deterministic_caption(selected_topic, brand_name)
                logger.info(
                    "Content engine DEGRADED: using deterministic caption "
                    "(LLM unavailable) topic='%s'",
                    selected_topic[:80],
                )
                result = await self._handle_unstructured_content(
                    caption=det,
                    topic=selected_topic,
                    category=category,
                    brand=brand,
                    brand_id=brand_id,
                    brand_name=brand_name,
                    started=started,
                )
                if isinstance(result, dict):
                    result["intelligence_mode"] = "degraded"
                    result["degraded"] = True
                    result["degraded_reason"] = "llm_unavailable"
                return result

            # ── Structured path succeeded ──
            caption = self._caption_builder.build(post, category, brand=brand)
            if not caption:
                caption = (
                    getattr(post, "caption", None)
                    or getattr(post, "title", None)
                    or selected_topic
                )
            caption = str(caption).strip()

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

        # CARD RENDERER
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

        # RENDER QUEUE
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

        # LEGACY IMAGE ENGINE FALLBACK
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
    # EMERGENCY FALLBACK
    # ═══════════════════════════════════════════════════════════════

    def get_fallback_post(self, topic: str = "", category: str = "", brand_id: str = "default") -> dict:
        caption = random.choice(FALLBACK_POSTS)
        artifact = MediaArtifact.no_media(topic=topic, brand_id=brand_id)
        return {
            "caption": caption,
            "image_bytes": None,
            "media_artifact": artifact,
            "topic": topic,
            "category": category,
            "theme": None,
            "visual_strategy": None,
            "media_status": artifact.status.value,
            "degraded": True,
            "publishable": False,
            "error_code": "CONTENT_GENERATION_FAILED",
        }

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
            return self.get_fallback_post(topic=topic or "", category="general")

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
