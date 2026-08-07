"""
Kanyoza Systems
Content Engine v14.0 — Brand-Aware Multimodal Content Pipeline

Canonical pipeline:

    Brand
      ↓
    Knowledge Base / Topic Selection
      ↓
    Content Strategy / Prompt
      ↓
    Gemini Structured Generation
      ↓
    TechPost Validation + Theme Normalization
      ↓
    Visual Strategy
      ↓
    CardRenderer / RenderQueue / ImageEngine
      ↓
    MediaArtifact
      ↓
    Publishing Pipeline

Design guarantees
=================

1. Topics are selected from the brand Knowledge Base when available.
2. Explicit topics always take precedence.
3. Hashtags come from brand configuration when available.
4. AI content is validated against the current TechPost contract.
5. A valid TechPost theme is NEVER passed as None.
6. Visual strategy is derived from the actual content/theme/brand.
7. Every render path returns MediaArtifact.
8. MEDIA_READY may be byte-backed, URL-backed, or both.
9. Rendering failure is represented by MediaArtifact.RENDER_FAILED.
10. AI-generation failure is distinct from rendering failure.
11. No successful main-pipeline result is returned without media.
12. Legacy callers remain supported.
13. MediaArtifact.ready_url() is NOT required.
14. URL-backed render results use MediaArtifact.ready(media_url=...).
15. Dashboard events report actual pipeline state.
16. Prompt cache is isolated by brand, AI profile and topic.
17. Existing renderer interfaces are preserved where possible.
"""

from __future__ import annotations

import hashlib
import logging
import random
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional, Tuple

from models.content import TechPost, PostTheme
from models.media_artifact import MediaArtifact, MediaStatus
from services.gemini import gemini
from utils.metrics import metrics


logger = logging.getLogger("kanyoza.services.content_engine")


# ============================================================================
# EMERGENCY FALLBACK TEXT
# ============================================================================

FALLBACK_POSTS = [
    "Stay tuned for more content coming soon! 🙏",
    "We're preparing something special for you. Check back shortly! ✨",
    "New content is being crafted. Follow us for updates! 🔥",
]


# ============================================================================
# SUPPORTED TECHPOST THEMES
# ============================================================================

# These values are taken from the current TechPost/PostTheme validation
# contract observed in production logs.

VALID_THEME_VALUES = {
    "comparison",
    "tutorial",
    "checklist",
    "architecture",
    "code_tips",
    "ai",
    "security",
    "database",
    "backend",
    "frontend",
    "system_design",
    "cloud",
}


# Theme aliases commonly returned by Gemini.
THEME_ALIASES = {
    "compare": "comparison",
    "comparison_post": "comparison",
    "comparisons": "comparison",
    "debate": "comparison",
    "versus": "comparison",
    "vs": "comparison",

    "how_to": "tutorial",
    "how-to": "tutorial",
    "guide": "tutorial",
    "guides": "tutorial",
    "tutorials": "tutorial",

    "tips": "code_tips",
    "coding_tips": "code_tips",
    "coding": "code_tips",
    "programming": "code_tips",

    "checklists": "checklist",

    "system-design": "system_design",
    "systemdesign": "system_design",

    "architecture_design": "architecture",

    "artificial_intelligence": "ai",
    "machine_learning": "ai",
    "machine-learning": "ai",

    "cybersecurity": "security",
    "cyber_security": "security",

    "db": "database",
    "databases": "database",

    "server": "backend",
    "servers": "backend",

    "web": "frontend",
    "ui": "frontend",
    "ux": "frontend",

    "cloud_computing": "cloud",
}


# ============================================================================
# VISUAL STRATEGY
# ============================================================================

class VisualStrategy(str, Enum):
    """Determines how generated content becomes media."""

    CARD = "card"
    AI_IMAGE = "ai_image"
    AUTO = "auto"


# ============================================================================
# NORMALIZED CONTENT RESULT
# ============================================================================

@dataclass
class ContentGenerationResult:
    """Normalized internal representation of generated content."""

    post: Optional[TechPost]
    caption: str
    topic: str
    category: str
    visual_strategy: VisualStrategy
    brand_id: str
    brand_name: str


# ============================================================================
# CAPTION BUILDER
# ============================================================================

class CaptionBuilder:
    """Build a social-media-ready caption from TechPost."""

    @staticmethod
    def _get_hashtags(
        post: TechPost,
        brand=None,
    ) -> str:
        """Resolve hashtags with brand configuration taking precedence."""

        if brand:

            formatted = getattr(
                brand,
                "formatted_hashtags",
                None,
            )

            if formatted:
                return str(formatted).strip()

            hashtags = getattr(
                brand,
                "hashtags",
                None,
            )

            if isinstance(
                hashtags,
                (list, tuple, set),
            ):
                return " ".join(
                    f"#{str(tag).lstrip('#').strip()}"
                    for tag in hashtags
                    if str(tag).strip()
                )

            if hashtags:
                return str(hashtags).strip()

        formatted = getattr(
            post,
            "formatted_hashtags",
            None,
        )

        if formatted:
            return str(formatted).strip()

        hashtags = getattr(
            post,
            "hashtags",
            None,
        )

        if isinstance(
            hashtags,
            (list, tuple, set),
        ):
            return " ".join(
                f"#{str(tag).lstrip('#').strip()}"
                for tag in hashtags
                if str(tag).strip()
            )

        if hashtags:
            return str(hashtags).strip()

        return ""

    @staticmethod
    def build(
        post: TechPost,
        category: str,
        brand=None,
    ) -> str:
        """Build the final Facebook/social caption."""

        tags = CaptionBuilder._get_hashtags(
            post,
            brand,
        )

        lines: list[str] = []

        title = getattr(
            post,
            "title",
            None,
        )

        if title:
            lines.append(
                f"🔥 {str(title).strip()}"
            )
            lines.append("")

        body = getattr(
            post,
            "caption",
            None,
        )

        if body:
            lines.append(
                str(body).strip()
            )
            lines.append("")

        is_debate = bool(
            getattr(
                post,
                "is_debate",
                False,
            )
        )

        option_a = getattr(
            post,
            "option_a",
            None,
        )

        option_b = getattr(
            post,
            "option_b",
            None,
        )

        if (
            is_debate
            and option_a
            and option_b
        ):
            lines.append(
                f"⚡ {str(option_a).strip()} vs "
                f"{str(option_b).strip()}:"
            )

            points_a = (
                getattr(post, "points_a", None)
                or []
            )

            points_b = (
                getattr(post, "points_b", None)
                or []
            )

            for point in points_a[:3]:
                if point:
                    lines.append(
                        f"  • {str(option_a).strip()}: "
                        f"{str(point).strip()}"
                    )

            for point in points_b[:3]:
                if point:
                    lines.append(
                        f"  • {str(option_b).strip()}: "
                        f"{str(point).strip()}"
                    )

            lines.append("")

        winner = getattr(
            post,
            "winner",
            None,
        )

        if winner:
            lines.append(
                f"🏆 Winner: {str(winner).strip()}"
            )
            lines.append("")

        if tags:
            lines.append(tags)

        return "\n".join(
            lines
        ).strip()


# ============================================================================
# CONTENT ENGINE
# ============================================================================

class ContentEngine:
    """
    Brand-aware multimodal content generation engine.
    """

    def __init__(
        self,
        container=None,
    ):
        self._container = container
        self._caption_builder = CaptionBuilder()

        # Local prompt cache.
        self._prompt_cache: Dict[str, str] = {}

        # Optional Redis prompt cache.
        self._redis = None

        if container:
            try:
                from core.redis_client import (
                    UpstashRedisClient,
                )

                self._redis = (
                    container.try_resolve(
                        UpstashRedisClient
                    )
                )

            except Exception as exc:
                logger.debug(
                    "Redis prompt cache unavailable: %s",
                    exc,
                )

    # ========================================================================
    # BRAND HELPERS
    # ========================================================================

    @staticmethod
    def _brand_id(
        brand,
    ) -> str:
        return str(
            getattr(
                brand,
                "id",
                None,
            )
            or getattr(
                brand,
                "slug",
                None,
            )
            or "default"
        )

    @staticmethod
    def _brand_name(
        brand,
    ) -> str:
        return str(
            getattr(
                brand,
                "name",
                None,
            )
            or getattr(
                brand,
                "brand_name",
                None,
            )
            or "Default"
        )

    # ========================================================================
    # DASHBOARD TELEMETRY
    # ========================================================================

    def _emit_dashboard_event(
        self,
        event_name: str,
        payload: dict,
    ) -> None:
        """
        Best-effort dashboard event emission.

        Telemetry must never break content generation.
        """

        try:
            import asyncio

            from core.socket_server import (
                emit_to_dashboard,
            )

            try:
                loop = asyncio.get_running_loop()

            except RuntimeError:
                logger.debug(
                    "No active event loop for dashboard event '%s'",
                    event_name,
                )
                return

            if loop.is_running():
                asyncio.create_task(
                    emit_to_dashboard(
                        event_name,
                        payload,
                    )
                )

        except Exception as exc:
            logger.debug(
                "Dashboard event '%s' failed: %s",
                event_name,
                exc,
            )

    # ========================================================================
    # PROMPT CACHE
    # ========================================================================

    @staticmethod
    def _cache_key(
        brand,
        ai_profile,
        topic: str,
    ) -> str:

        brand_id = str(
            getattr(
                brand,
                "id",
                None,
            )
            or getattr(
                brand,
                "slug",
                None,
            )
            or "default"
        )

        profile_id = str(
            getattr(
                ai_profile,
                "id",
                None,
            )
            or getattr(
                ai_profile,
                "slug",
                None,
            )
            or getattr(
                ai_profile,
                "name",
                None,
            )
            or "default"
        )

        normalized_topic = " ".join(
            str(topic)
            .lower()
            .split()
        )

        digest = hashlib.sha256(
            normalized_topic.encode(
                "utf-8"
            )
        ).hexdigest()[:24]

        return (
            "kanyoza:prompt:"
            f"{brand_id}:"
            f"{profile_id}:"
            f"{digest}"
        )

    def _cached_prompt(
        self,
        brand,
        ai_profile,
        topic: str,
    ) -> Optional[str]:

        key = self._cache_key(
            brand,
            ai_profile,
            topic,
        )

        if self._redis:

            try:
                if self._redis.is_available:

                    cached = self._redis.get(
                        key
                    )

                    if cached:
                        logger.debug(
                            "Prompt cache hit: %s",
                            key,
                        )

                        return cached

            except Exception as exc:
                logger.debug(
                    "Redis prompt lookup failed: %s",
                    exc,
                )

        return self._prompt_cache.get(
            key
        )

    def _cache_prompt(
        self,
        brand,
        ai_profile,
        topic: str,
        prompt: str,
    ) -> None:

        key = self._cache_key(
            brand,
            ai_profile,
            topic,
        )

        self._prompt_cache[
            key
        ] = prompt

        if self._redis:

            try:
                if self._redis.is_available:
                    self._redis.set(
                        key,
                        prompt,
                        ex=1800,
                    )

            except Exception as exc:
                logger.debug(
                    "Redis prompt cache write failed: %s",
                    exc,
                )

    # ========================================================================
    # THEME NORMALIZATION
    # ========================================================================

    @classmethod
    def _normalize_theme(
        cls,
        raw_theme: Any,
        *,
        post_data: Optional[dict] = None,
        topic: str = "",
    ) -> str:
        """
        Convert Gemini's theme into a valid TechPost theme.

        CRITICAL:
            Never return None.

        Current valid values are:

            comparison
            tutorial
            checklist
            architecture
            code_tips
            ai
            security
            database
            backend
            frontend
            system_design
            cloud
        """

        value = raw_theme

        if hasattr(
            value,
            "value",
        ):
            value = value.value

        if value is not None:

            normalized = (
                str(value)
                .strip()
                .lower()
                .replace(" ", "_")
            )

            normalized = (
                THEME_ALIASES.get(
                    normalized,
                    normalized,
                )
            )

            if normalized in VALID_THEME_VALUES:
                return normalized

        # ------------------------------------------------------------
        # Infer from structured fields.
        # ------------------------------------------------------------

        data = post_data or {}

        if (
            data.get("option_a")
            and data.get("option_b")
        ):
            return "comparison"

        # ------------------------------------------------------------
        # Infer from topic.
        # ------------------------------------------------------------

        topic_lower = (
            str(topic)
            .lower()
        )

        if any(
            word in topic_lower
            for word in (
                "security",
                "cybersecurity",
                "cyber security",
                "hack",
                "vulnerability",
            )
        ):
            return "security"

        if any(
            word in topic_lower
            for word in (
                "database",
                "sql",
                "mysql",
                "postgres",
                "supabase",
            )
        ):
            return "database"

        if any(
            word in topic_lower
            for word in (
                "frontend",
                "react",
                "vue",
                "angular",
                "css",
                "html",
            )
        ):
            return "frontend"

        if any(
            word in topic_lower
            for word in (
                "backend",
                "api",
                "django",
                "flask",
                "fastapi",
                "laravel",
            )
        ):
            return "backend"

        if any(
            word in topic_lower
            for word in (
                "cloud",
                "aws",
                "azure",
                "gcp",
                "serverless",
            )
        ):
            return "cloud"

        if any(
            word in topic_lower
            for word in (
                "architecture",
                "microservice",
                "microservices",
            )
        ):
            return "architecture"

        if any(
            word in topic_lower
            for word in (
                "system design",
                "distributed system",
                "distributed systems",
            )
        ):
            return "system_design"

        if any(
            word in topic_lower
            for word in (
                "ai",
                "artificial intelligence",
                "machine learning",
                "machine-learning",
                "gemini",
                "llm",
            )
        ):
            return "ai"

        if any(
            word in topic_lower
            for word in (
                "tutorial",
                "how to",
                "guide",
                "learn",
            )
        ):
            return "tutorial"

        if any(
            word in topic_lower
            for word in (
                "checklist",
                "steps",
                "things to",
            )
        ):
            return "checklist"

        if any(
            word in topic_lower
            for word in (
                "tips",
                "coding",
                "programming",
                "developer",
            )
        ):
            return "code_tips"

        # ------------------------------------------------------------
        # Safe deterministic default.
        #
        # This is preferable to passing None into Pydantic.
        # ------------------------------------------------------------

        return "tutorial"

    @classmethod
    def _theme_enum(
        cls,
        raw_theme: Any,
        *,
        post_data: Optional[dict] = None,
        topic: str = "",
    ):
        """
        Return a valid PostTheme enum when possible.

        Falls back to the normalized string if the enum implementation
        differs between deployments.
        """

        normalized = cls._normalize_theme(
            raw_theme,
            post_data=post_data,
            topic=topic,
        )

        try:
            return PostTheme(
                normalized
            )

        except Exception:
            return normalized

    # ========================================================================
    # POST NORMALIZATION
    # ========================================================================

    def _normalize_structured_payload(
        self,
        structured: Any,
        topic: str,
    ) -> Any:
        """
        Normalize Gemini's structured payload before TechPost validation.

        Handles dictionaries as well as JSON-like objects.
        """

        if structured is None:
            return None

        # Pydantic model / object.
        if hasattr(
            structured,
            "model_dump",
        ):
            try:
                data = structured.model_dump(
                    mode="python"
                )
            except Exception:
                data = None
        elif isinstance(
            structured,
            dict,
        ):
            data = dict(
                structured
            )
        else:
            data = None

        if data is None:
            return structured

        # Normalize theme before Pydantic sees it.
        data["theme"] = self._normalize_theme(
            data.get("theme"),
            post_data=data,
            topic=topic,
        )

        return data

    # ========================================================================
    # VISUAL STRATEGY
    # ========================================================================

    def _resolve_visual_strategy(
        self,
        post: TechPost,
        brand=None,
    ) -> VisualStrategy:

        # ------------------------------------------------------------
        # 1. Explicit brand configuration
        # ------------------------------------------------------------

        configured = None

        if brand:
            configured = (
                getattr(
                    brand,
                    "visual_strategy",
                    None,
                )
                or getattr(
                    brand,
                    "image_strategy",
                    None,
                )
                or getattr(
                    brand,
                    "render_strategy",
                    None,
                )
            )

        if configured:

            try:
                return VisualStrategy(
                    str(
                        configured
                    ).lower()
                )

            except ValueError:
                logger.warning(
                    "Unknown brand visual strategy '%s'; "
                    "using automatic strategy",
                    configured,
                )

        # ------------------------------------------------------------
        # 2. Theme strategy
        # ------------------------------------------------------------

        theme_value = self._theme_value(
            post
        )

        card_themes = {
            "comparison",
            "tutorial",
            "checklist",
            "architecture",
            "code_tips",
            "security",
            "database",
            "backend",
            "frontend",
            "system_design",
            "cloud",
        }

        if theme_value in card_themes:
            return VisualStrategy.CARD

        # ------------------------------------------------------------
        # 3. Structural comparison clues
        # ------------------------------------------------------------

        if (
            getattr(
                post,
                "option_a",
                None,
            )
            and getattr(
                post,
                "option_b",
                None,
            )
        ):
            return VisualStrategy.CARD

        # ------------------------------------------------------------
        # 4. Brand AI imagery
        # ------------------------------------------------------------

        if brand:

            ai_images_enabled = getattr(
                brand,
                "ai_images_enabled",
                None,
            )

            if ai_images_enabled is True:
                return VisualStrategy.AI_IMAGE

        # ------------------------------------------------------------
        # 5. Safe deterministic default
        # ------------------------------------------------------------

        return VisualStrategy.CARD

    # ========================================================================
    # TOPIC SELECTION
    # ========================================================================

    def _select_topic(
        self,
        brand=None,
        topic: Optional[str] = None,
    ) -> Tuple[str, str]:

        if topic and str(
            topic
        ).strip():

            return (
                str(topic).strip(),
                "general",
            )

        try:

            from domain.content.topic_selector import (
                TopicSelector,
            )

            knowledge_repo = None

            if self._container:

                try:
                    from repositories.knowledge_repository import (
                        KnowledgeRepository,
                    )

                    knowledge_repo = (
                        self._container.try_resolve(
                            KnowledgeRepository
                        )
                    )

                except Exception as exc:
                    logger.debug(
                        "KnowledgeRepository unavailable: %s",
                        exc,
                    )

            selector = TopicSelector(
                brand=brand,
                knowledge_repo=knowledge_repo,
            )

            selected = selector.pick()

            if not selected:
                raise ValueError(
                    "TopicSelector returned no topic"
                )

            selected_topic, category = selected

            if not selected_topic:
                raise ValueError(
                    "TopicSelector returned an empty topic"
                )

            return (
                str(
                    selected_topic
                ).strip(),
                str(
                    category
                    or "general"
                ).strip(),
            )

        except Exception:
            logger.exception(
                "Topic selection failed"
            )
            raise

    # ========================================================================
    # PROMPT CONSTRUCTION
    # ========================================================================

    def _build_prompt(
        self,
        brand,
        ai_profile,
        topic: str,
        base_prompt: Optional[str],
    ) -> str:

        if base_prompt:
            return str(
                base_prompt
            ).strip()

        cached = self._cached_prompt(
            brand,
            ai_profile,
            topic,
        )

        if cached:
            return cached

        from services.prompt_builder import (
            prompt_builder,
        )

        prompt = prompt_builder.build_post_prompt(
            brand,
            ai_profile,
            topic,
        )

        if prompt:
            self._cache_prompt(
                brand,
                ai_profile,
                topic,
                prompt,
            )

        return str(
            prompt or ""
        ).strip()

    # ========================================================================
    # STRUCTURED AI GENERATION
    # ========================================================================

    def _generate_structured_content(
        self,
        topic: str,
        prompt: str,
    ) -> Optional[TechPost]:

        structured = gemini.generate_structured_post(
            topic,
            None,
            base_prompt=prompt,
        )

        if not structured:
            return None

        try:

            normalized = (
                self._normalize_structured_payload(
                    structured,
                    topic,
                )
            )

            # --------------------------------------------------------
            # IMPORTANT
            #
            # Do NOT call:
            #
            #   from_gemini_response(..., theme=None)
            #
            # because current TechPost requires a valid enum value.
            # --------------------------------------------------------

            try:

                post = TechPost.from_gemini_response(
                    normalized
                )

            except TypeError:

                # Compatibility with an older signature that accepts
                # an optional theme argument.
                normalized_theme = (
                    normalized.get("theme")
                    if isinstance(
                        normalized,
                        dict,
                    )
                    else self._normalize_theme(
                        None,
                        topic=topic,
                    )
                )

                post = TechPost.from_gemini_response(
                    normalized,
                    theme=self._theme_enum(
                        normalized_theme,
                        post_data=(
                            normalized
                            if isinstance(
                                normalized,
                                dict,
                            )
                            else None
                        ),
                        topic=topic,
                    ),
                )

            if not post:
                return None

            # --------------------------------------------------------
            # Final defense: make absolutely sure the returned model
            # contains a valid theme.
            # --------------------------------------------------------

            current_theme = getattr(
                post,
                "theme",
                None,
            )

            normalized_theme = (
                self._normalize_theme(
                    current_theme,
                    post_data=(
                        normalized
                        if isinstance(
                            normalized,
                            dict,
                        )
                        else None
                    ),
                    topic=topic,
                )
            )

            # If the Pydantic model allows assignment, normalize it.
            try:
                post.theme = self._theme_enum(
                    normalized_theme,
                    post_data=(
                        normalized
                        if isinstance(
                            normalized,
                            dict,
                        )
                        else None
                    ),
                    topic=topic,
                )
            except Exception:
                # Some Pydantic configurations may be frozen.
                # In that case the validated object is still acceptable
                # if its existing theme is valid.
                pass

            return post

        except Exception as exc:

            logger.warning(
                "Structured AI response validation failed: %s",
                exc,
            )

            return None

    # ========================================================================
    # PUBLIC GENERATION API
    # ========================================================================

    def generate_post(
        self,
        brand=None,
        ai_profile=None,
        topic: Optional[str] = None,
        base_prompt: Optional[str] = None,
    ) -> dict:
        """
        Generate a complete multimodal post.

        Successful contract:

            {
                caption,
                image_bytes,
                media_artifact,
                topic,
                category,
                theme,
                visual_strategy
            }

        A successful result always contains a publishable MediaArtifact.

        Failed generation is explicitly marked non-publishable.
        """

        started = time.monotonic()

        brand_id = self._brand_id(
            brand
        )

        brand_name = self._brand_name(
            brand
        )

        selected_topic = (
            str(topic or "")
            .strip()
        )

        category = "general"

        try:

            # ================================================================
            # 1. TOPIC
            # ================================================================

            (
                selected_topic,
                category,
            ) = self._select_topic(
                brand=brand,
                topic=topic,
            )

            logger.info(
                "Pipeline start: brand='%s' "
                "topic='%s' category='%s'",
                brand_name,
                selected_topic[:100],
                category,
            )

            self._emit_dashboard_event(
                "ai_decision",
                {
                    "stage": "topic_selected",
                    "topic": selected_topic,
                    "category": category,
                    "brand_id": brand_id,
                    "brand_name": brand_name,
                    "timestamp": self._utc_now(),
                },
            )

            # ================================================================
            # 2. PROMPT
            # ================================================================

            prompt = self._build_prompt(
                brand=brand,
                ai_profile=ai_profile,
                topic=selected_topic,
                base_prompt=base_prompt,
            )

            if not prompt:
                raise ValueError(
                    "Prompt construction returned an empty prompt"
                )

            # ================================================================
            # 3. AI GENERATION
            # ================================================================

            self._emit_dashboard_event(
                "ai_decision",
                {
                    "stage": "content_generation_started",
                    "topic": selected_topic,
                    "category": category,
                    "brand_id": brand_id,
                    "brand_name": brand_name,
                    "timestamp": self._utc_now(),
                },
            )

            post = (
                self._generate_structured_content(
                    topic=selected_topic,
                    prompt=prompt,
                )
            )

            # ================================================================
            # 4. STRUCTURED GENERATION FAILURE
            # ================================================================

            if post is None:

                logger.warning(
                    "Structured generation failed for topic='%s'",
                    selected_topic,
                )

                # Secondary AI path.
                fallback_body = (
                    gemini.generate_content_post(
                        topic=selected_topic,
                        template_type="deep_dive",
                    )
                )

                if fallback_body:

                    logger.warning(
                        "AI returned unstructured content. "
                        "Attempting AI visual fallback."
                    )

                    return (
                        self._handle_unstructured_content(
                            caption=str(
                                fallback_body
                            ),
                            topic=selected_topic,
                            category=category,
                            brand=brand,
                            brand_id=brand_id,
                            brand_name=brand_name,
                            started=started,
                        )
                    )

                return (
                    self._failed_generation_result(
                        topic=selected_topic,
                        category=category,
                        brand_id=brand_id,
                        brand_name=brand_name,
                        started=started,
                        reason=(
                            "AI generation returned "
                            "no usable content"
                        ),
                    )
                )

            # ================================================================
            # 5. CAPTION
            # ================================================================

            caption = (
                self._caption_builder.build(
                    post,
                    category,
                    brand=brand,
                )
            )

            if not caption:

                caption = (
                    getattr(
                        post,
                        "caption",
                        None,
                    )
                    or getattr(
                        post,
                        "title",
                        None,
                    )
                    or selected_topic
                )

            caption = str(
                caption
            ).strip()

            # ================================================================
            # 6. VISUAL STRATEGY
            # ================================================================

            strategy = (
                self._resolve_visual_strategy(
                    post,
                    brand=brand,
                )
            )

            logger.info(
                "Visual strategy selected: strategy='%s' "
                "theme='%s' brand='%s'",
                strategy.value,
                self._theme_value(post),
                brand_name,
            )

            self._emit_dashboard_event(
                "ai_decision",
                {
                    "stage": "visual_strategy_selected",
                    "strategy": strategy.value,
                    "theme": self._theme_value(post),
                    "topic": selected_topic,
                    "brand_id": brand_id,
                    "brand_name": brand_name,
                    "timestamp": self._utc_now(),
                },
            )

            # ================================================================
            # 7. RENDER
            # ================================================================

            media_artifact = (
                self._render_image(
                    post=post,
                    topic=selected_topic,
                    brand=brand,
                    strategy=strategy,
                )
            )

            # ================================================================
            # 8. METRICS
            # ================================================================

            elapsed_ms = (
                time.monotonic()
                - started
            ) * 1000

            success = bool(
                media_artifact
                and media_artifact.is_publishable
            )

            metrics.record_ai(
                "content_engine",
                "generate_post",
                elapsed_ms,
                success,
            )

            # ================================================================
            # 9. DASHBOARD
            # ================================================================

            media_status = (
                media_artifact.status.value
                if media_artifact
                else MediaStatus.RENDER_FAILED.value
            )

            self._emit_dashboard_event(
                "ai_decision",
                {
                    "stage": (
                        "pipeline_complete"
                        if success
                        else "render_failed"
                    ),
                    "topic": selected_topic,
                    "category": category,
                    "theme": self._theme_value(post),
                    "visual_strategy": strategy.value,
                    "media_status": media_status,
                    "artifact_id": (
                        media_artifact.artifact_id
                        if media_artifact
                        else None
                    ),
                    "brand_id": brand_id,
                    "brand_name": brand_name,
                    "duration_ms": round(
                        elapsed_ms,
                        2,
                    ),
                    "timestamp": self._utc_now(),
                },
            )

            # ================================================================
            # 10. RETURN
            # ================================================================

            return {
                "caption": caption,

                # Compatibility field.
                # URL-backed artifacts intentionally have None here.
                "image_bytes": (
                    media_artifact.bytes
                    if (
                        media_artifact
                        and media_artifact.has_bytes
                    )
                    else None
                ),

                "media_artifact": media_artifact,

                "topic": selected_topic,
                "category": category,
                "theme": self._theme_value(post),
                "visual_strategy": strategy.value,

                "publishable": success,
                "degraded": not success,

                "media_status": (
                    media_status
                ),
            }

        except Exception as exc:

            elapsed_ms = (
                time.monotonic()
                - started
            ) * 1000

            logger.error(
                "Post generation crashed: "
                "brand='%s' error=%s",
                brand_name,
                exc,
                exc_info=True,
            )

            metrics.record_ai(
                "content_engine",
                "generate_post",
                elapsed_ms,
                False,
            )

            return (
                self._failed_generation_result(
                    topic=selected_topic,
                    category=category,
                    brand_id=brand_id,
                    brand_name=brand_name,
                    started=started,
                    reason=str(exc),
                )
            )

    # ========================================================================
    # UNSTRUCTURED CONTENT
    # ========================================================================

    def _handle_unstructured_content(
        self,
        caption: str,
        topic: str,
        category: str,
        brand,
        brand_id: str,
        brand_name: str,
        started: float,
    ) -> dict:
        """
        Convert unstructured AI text into an AI-generated visual.

        Text-only success is intentionally forbidden.
        """

        try:

            image_bytes = (
                self._generate_ai_image(
                    topic=topic,
                    caption=caption,
                    brand=brand,
                )
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
                    "visual_strategy": (
                        VisualStrategy.AI_IMAGE.value
                    ),
                    "publishable": artifact.is_publishable,
                    "degraded": False,
                    "media_status": artifact.status.value,
                }

        except Exception as exc:

            logger.warning(
                "Unstructured-content visual generation failed: %s",
                exc,
            )

        return (
            self._failed_generation_result(
                topic=topic,
                category=category,
                brand_id=brand_id,
                brand_name=brand_name,
                started=started,
                reason=(
                    "Unstructured AI content "
                    "could not be rendered"
                ),
                caption=caption,
            )
        )

    # ========================================================================
    # RENDERING
    # ========================================================================

    def _render_image(
        self,
        post: TechPost,
        topic: str,
        brand=None,
        strategy: VisualStrategy = VisualStrategy.AUTO,
    ) -> MediaArtifact:
        """
        Render media through the canonical rendering hierarchy.

        CARD:

            CardRenderer
              ↓
            RenderQueue
              ↓
            ImageEngine

        AI_IMAGE:

            ImageEngine
              ↓
            CardRenderer
              ↓
            RenderQueue
              ↓
            ImageEngine legacy fallback

        Every path returns MediaArtifact.
        """

        brand_id = self._brand_id(
            brand
        )

        brand_name = self._brand_name(
            brand
        )

        caption = (
            CaptionBuilder.build(
                post,
                self._theme_value(post),
                brand=brand,
            )
        )

        if strategy == VisualStrategy.AUTO:
            strategy = (
                self._resolve_visual_strategy(
                    post,
                    brand=brand,
                )
            )

        # ====================================================================
        # AI IMAGE
        # ====================================================================

        if strategy == VisualStrategy.AI_IMAGE:

            try:

                image_bytes = (
                    self._generate_ai_image(
                        topic=topic,
                        caption=caption,
                        brand=brand,
                        post=post,
                    )
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

                logger.warning(
                    "AI image strategy failed: "
                    "brand='%s' topic='%s' error=%s",
                    brand_name,
                    topic[:60],
                    exc,
                )

            logger.info(
                "AI image failed; attempting "
                "branded card fallback"
            )

        # ====================================================================
        # CARD RENDERER
        # ====================================================================

        try:

            from services.card_renderer import (
                card_renderer,
            )

            image_bytes = (
                card_renderer.render(
                    post,
                    brand=brand,
                )
            )

            if image_bytes:

                logger.info(
                    "CardRenderer success: "
                    "brand='%s' theme='%s' "
                    "topic='%s' size=%dKB",
                    brand_name,
                    self._theme_value(post),
                    topic[:50],
                    len(image_bytes) // 1024,
                )

                return MediaArtifact.ready(
                    image_bytes=image_bytes,
                    content_type="image/png",
                    source="pillow",
                    topic=topic,
                    brand_id=brand_id,
                )

        except Exception as exc:

            logger.warning(
                "CardRenderer failed: "
                "brand='%s' topic='%s' error=%s",
                brand_name,
                topic[:60],
                exc,
            )

        # ====================================================================
        # RENDER QUEUE
        # ====================================================================

        try:

            from core.render_queue import (
                get_render_queue,
                RenderTask,
            )

            from services.card_renderer import (
                card_renderer,
            )

            post_dict = (
                post.model_dump(
                    mode="json"
                )
            )

            post_dict.update(
                {
                    "brand_name": brand_name,

                    "brand_hashtags": (
                        getattr(
                            brand,
                            "formatted_hashtags",
                            "",
                        )
                        if brand
                        else ""
                    ),

                    "brand_colors": (
                        getattr(
                            brand,
                            "colors",
                            None,
                        )
                        or {}
                    )
                    if brand
                    else {},

                    "brand_metadata": (
                        getattr(
                            brand,
                            "metadata",
                            None,
                        )
                        or {}
                    )
                    if brand
                    else {},

                    "visual_strategy": (
                        strategy.value
                    ),
                }
            )

            html = ""

            try:

                html = (
                    card_renderer.render_html_only(
                        post,
                        brand=brand,
                    )
                    or ""
                )

            except Exception as exc:

                logger.debug(
                    "HTML preview generation failed: %s",
                    exc,
                )

            task = RenderTask(
                brand_id=brand_id,
                caption=caption,
                html=html,
                topic=topic,
                post_dict=post_dict,
            )

            queue = get_render_queue()

            task_id = (
                queue.enqueue_render(
                    task
                )
            )

            logger.info(
                "RenderQueue waiting: "
                "brand='%s' topic='%s' task_id=%s",
                brand_name,
                topic[:60],
                task_id,
            )

            result = (
                queue.wait_for_render(
                    task_id,
                    timeout=60,
                )
            )

            if result:

                # ------------------------------------------------------------
                # Byte-backed result
                # ------------------------------------------------------------

                image_bytes = getattr(
                    result,
                    "image_bytes",
                    None,
                )

                if (
                    image_bytes
                    and len(image_bytes) > 0
                ):

                    return MediaArtifact.ready(
                        image_bytes=image_bytes,
                        content_type=(
                            getattr(
                                result,
                                "content_type",
                                None,
                            )
                            or "image/png"
                        ),
                        width=(
                            getattr(
                                result,
                                "width",
                                None,
                            )
                            or 1080
                        ),
                        height=(
                            getattr(
                                result,
                                "height",
                                None,
                            )
                            or 1080
                        ),
                        source="render_queue",
                        topic=topic,
                        brand_id=brand_id,
                        task_id=str(
                            task_id
                        ),
                        render_time_ms=(
                            getattr(
                                result,
                                "render_time_ms",
                                None,
                            )
                        ),
                    )

                # ------------------------------------------------------------
                # URL-backed result
                #
                # IMPORTANT:
                #
                # Current MediaArtifact does NOT have ready_url().
                #
                # Correct:
                #
                # MediaArtifact.ready(
                #     media_url=image_url
                # )
                # ------------------------------------------------------------

                image_url = getattr(
                    result,
                    "image_url",
                    None,
                )

                if not image_url:

                    image_url = getattr(
                        result,
                        "url",
                        None,
                    )

                if image_url:

                    return MediaArtifact.ready(
                        media_url=str(
                            image_url
                        ),
                        content_type=(
                            getattr(
                                result,
                                "content_type",
                                None,
                            )
                            or "image/png"
                        ),
                        width=(
                            getattr(
                                result,
                                "width",
                                None,
                            )
                            or 1080
                        ),
                        height=(
                            getattr(
                                result,
                                "height",
                                None,
                            )
                            or 1080
                        ),
                        source="render_queue",
                        topic=topic,
                        brand_id=brand_id,
                        task_id=str(
                            task_id
                        ),
                        render_time_ms=(
                            getattr(
                                result,
                                "render_time_ms",
                                None,
                            )
                        ),
                    )

            logger.warning(
                "RenderQueue produced no usable media: "
                "brand='%s' topic='%s'",
                brand_name,
                topic[:60],
            )

        except Exception as exc:

            logger.warning(
                "RenderQueue failed: "
                "brand='%s' topic='%s' error=%s",
                brand_name,
                topic[:60],
                exc,
            )

        # ====================================================================
        # LEGACY IMAGE ENGINE FALLBACK
        # ====================================================================

        try:

            image_bytes = (
                self._generate_ai_image(
                    topic=topic,
                    caption=caption,
                    brand=brand,
                    post=post,
                )
            )

            if image_bytes:

                logger.info(
                    "ImageEngine fallback success: "
                    "brand='%s' topic='%s'",
                    brand_name,
                    topic[:60],
                )

                return MediaArtifact.ready(
                    image_bytes=image_bytes,
                    content_type="image/png",
                    source="image_engine",
                    topic=topic,
                    brand_id=brand_id,
                )

        except Exception as exc:

            logger.warning(
                "ImageEngine fallback failed: "
                "brand='%s' topic='%s' error=%s",
                brand_name,
                topic[:60],
                exc,
            )

        # ====================================================================
        # TOTAL RENDER FAILURE
        # ====================================================================

        logger.error(
            "RENDER_FAILED: all visual strategies exhausted. "
            "brand='%s' topic='%s'",
            brand_name,
            topic[:60],
        )

        return MediaArtifact.failed(
            error=(
                "All rendering methods exhausted: "
                "CardRenderer, RenderQueue, ImageEngine"
            ),
            error_code="RENDER_FAILED",
            source=str(
                strategy.value
            ),
            topic=topic,
            brand_id=brand_id,
        )

    # ========================================================================
    # AI IMAGE ENGINE
    # ========================================================================

    def _generate_ai_image(
        self,
        topic: str,
        caption: str,
        brand=None,
        post: Optional[TechPost] = None,
    ) -> Optional[bytes]:
        """
        Delegate image generation to image_engine.

        Provider/model selection remains owned by image_engine.
        """

        from services.image_engine import (
            generate_best_image,
        )

        theme = (
            self._theme_value(post)
            if post
            else "ai"
        )

        brand_name = (
            self._brand_name(
                brand
            )
        )

        return generate_best_image(
            topic,
            (
                f"{caption[:500]} | "
                f"Brand: {brand_name}"
            ),
            theme,
        )

    # ========================================================================
    # FAILURE RESULT
    # ========================================================================

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

        elapsed_ms = (
            time.monotonic()
            - started
        ) * 1000

        logger.error(
            "CONTENT_PIPELINE_FAILED: "
            "brand='%s' topic='%s' reason=%s",
            brand_name,
            topic[:80],
            reason,
        )

        artifact = MediaArtifact.failed(
            error=reason,
            error_code="CONTENT_PIPELINE_FAILED",
            source="content_engine",
            topic=topic,
            brand_id=brand_id,
            render_time_ms=elapsed_ms,
        )

        self._emit_dashboard_event(
            "ai_decision",
            {
                "stage": "pipeline_failed",
                "topic": topic,
                "category": category,
                "brand_id": brand_id,
                "brand_name": brand_name,
                "error": reason,
                "media_status": (
                    artifact.status.value
                ),
                "artifact_id": (
                    artifact.artifact_id
                ),
                "duration_ms": round(
                    elapsed_ms,
                    2,
                ),
                "timestamp": self._utc_now(),
            },
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

            "media_status": (
                artifact.status.value
            ),

            "error_code": (
                "CONTENT_PIPELINE_FAILED"
            ),
            "error": reason,
        }

    # ========================================================================
    # EMERGENCY FALLBACK
    # ========================================================================

    def get_fallback_post(
        self,
        topic: str = "",
        category: str = "",
        brand_id: str = "default",
    ) -> dict:
        """
        Absolute emergency fallback.

        This intentionally returns NO_MEDIA and is NOT publishable.
        """

        caption = random.choice(
            FALLBACK_POSTS
        )

        artifact = (
            MediaArtifact.no_media(
                topic=topic,
                brand_id=brand_id,
            )
        )

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
            "error_code": (
                "CONTENT_GENERATION_FAILED"
            ),
        }

    # ========================================================================
    # LEGACY API
    # ========================================================================

    def generate_post_legacy(
        self,
        topic: Optional[str] = None,
    ) -> dict:
        """
        Backward-compatible entry point for old callers.
        """

        try:

            from models.brand import (
                Brand,
            )

            from models.ai_profile import (
                AIProfile,
            )

            default_brand = (
                Brand.default()
            )

            default_profile = (
                AIProfile.default()
            )

            return self.generate_post(
                brand=default_brand,
                ai_profile=default_profile,
                topic=topic,
            )

        except Exception as exc:

            logger.warning(
                "Default brand/profile initialization failed: %s",
                exc,
            )

            return self.get_fallback_post(
                topic=topic or "",
                category="general",
            )

    # ========================================================================
    # HELPERS
    # ========================================================================

    @staticmethod
    def _theme_value(
        post: Optional[TechPost],
    ) -> str:

        if not post:
            return ""

        theme = getattr(
            post,
            "theme",
            None,
        )

        if hasattr(
            theme,
            "value",
        ):
            return str(
                theme.value
            )

        return str(
            theme or ""
        ).strip()

    @staticmethod
    def _utc_now() -> str:

        from datetime import (
            datetime,
            timezone,
        )

        return datetime.now(
            timezone.utc
        ).isoformat()


# ============================================================================
# MODULE SINGLETON
# ============================================================================

content_engine = ContentEngine(
    container=None
)


__all__ = [
    "ContentEngine",
    "ContentGenerationResult",
    "CaptionBuilder",
    "VisualStrategy",
    "content_engine",
]
