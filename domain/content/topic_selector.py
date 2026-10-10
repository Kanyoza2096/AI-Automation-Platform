from __future__ import annotations

import random
import logging
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from models.brand import Brand

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Sentinel category for the tier-6 fallback
# ═══════════════════════════════════════════════════════════════════════════
#
# When a brand has no direction, no custom_topics, no brand_config, no KB
# entries, and no platform fallback, TopicSelector returns a topic from the
# last-resort list. That list is intentionally honest — it says "this brand
# has not been configured" rather than inventing plausible-sounding topics
# that the pipeline would happily publish.
#
# Callers that want to skip unconfigured brands can check
# `category == UNCONFIGURED_CATEGORY` and refuse to publish.
#
UNCONFIGURED_CATEGORY = "unconfigured"

# Single honest fallback. Not five generic prompts. If a brand reaches this
# tier, the right behavior is to surface the gap, not to fill it with
# content the operator never chose.
_UNCONFIGURED_TOPICS: list[str] = [
    "This brand has no topic source configured; add topic_direction, "
    "custom_topics, brand_config.fallback_topics, or Knowledge Base entries.",
]


def _build_categories_from_knowledge_base(brand, knowledge_repo=None) -> Optional[dict[str, list[str]]]:
    """
    Query the brand's Knowledge Base and build topics from it.

    Generates both direct topics (from titles) and comparison-style topics
    (from related entries) for brands that use comparison format.

    Returns None if no KB data is available.
    """
    if not brand or not getattr(brand, 'id', None):
        return None

    if knowledge_repo is None:
        return None

    try:
        entries = knowledge_repo.find_by_brand(brand.id)

        if not entries:
            logger.info(
                "TopicSelector: No KB entries for brand '%s' (id=%s)",
                getattr(brand, 'name', 'unknown'), brand.id,
            )
            return None

        kb_categories: dict[str, list[str]] = {}

        for entry in entries:
            cat = getattr(entry, 'doc_type', None) or 'general'
            title = getattr(entry, 'title', None) or ''
            content = getattr(entry, 'content', None) or ''

            # Use title as the primary topic
            if title.strip():
                topic = title.strip()
            elif content.strip():
                first_sentence = content.strip().split('.')[0].strip()
                topic = first_sentence[:100] if len(first_sentence) > 100 else first_sentence
            else:
                continue

            cat_display = cat.replace('_', ' ').title()
            if cat_display not in kb_categories:
                kb_categories[cat_display] = []
            if topic not in kb_categories[cat_display]:
                kb_categories[cat_display].append(topic)

        # Generate comparison topics from related entries
        # (useful for professional/technical brands)
        for cat_name, topics in list(kb_categories.items()):
            if len(topics) >= 2:
                for i in range(len(topics) - 1):
                    for j in range(i + 1, min(i + 4, len(topics))):
                        vs_topic = f"{topics[i]} vs {topics[j]}"
                        if vs_topic not in kb_categories[cat_name]:
                            kb_categories[cat_name].append(vs_topic)

        if kb_categories:
            total = sum(len(v) for v in kb_categories.values())
            logger.info(
                "TopicSelector: Loaded %d topics across %d categories from KB for brand '%s'",
                total, len(kb_categories), getattr(brand, 'name', 'unknown'),
            )
            return kb_categories

    except Exception as e:
        logger.warning(
            "TopicSelector: KB query failed for brand '%s': %s",
            getattr(brand, 'name', 'unknown'), e,
        )

    return None


def _categories_from_brand_config(brand) -> Optional[dict[str, list[str]]]:
    """Load fallback_topics and keywords from brand_config table / runtime_config."""
    if not brand:
        return None
    brand_id = getattr(brand, "id", None) or getattr(brand, "brand_id", None)
    if not brand_id:
        return None
    workspace_id = (
        getattr(brand, "workspace_id", None)
        or getattr(brand, "tenant_id", None)
        or "default"
    )
    try:
        from core.brand_content_config import get_brand_content_config
        cfg = get_brand_content_config(str(brand_id), str(workspace_id))
    except Exception as exc:
        logger.debug("brand_config load failed: %s", exc)
        return None

    categories: dict[str, list[str]] = {}
    topics = list(cfg.fallback_topics or [])
    if topics:
        if cfg.content_categories:
            categories = {str(c): [] for c in cfg.content_categories}
            cats = list(categories.keys()) or ["Brand Topics"]
            for i, t in enumerate(topics):
                categories[cats[i % len(cats)]].append(str(t))
        else:
            categories["Brand Topics"] = [str(t) for t in topics]

    keywords = list(cfg.keywords or [])
    if keywords:
        kw_topics = [f"Thoughts on {kw}" for kw in keywords if kw]
        if "Keywords" not in categories:
            categories["Keywords"] = []
        categories["Keywords"].extend(kw_topics)

    categories = {k: v for k, v in categories.items() if v}
    return categories or None


# ─────────────────────────────────────────────────────────────────────────────
# Direction-based topic configuration
# ─────────────────────────────────────────────────────────────────────────────

# Keys the direction-generation path reads off the brand. On the brands table
# they live as first-class columns (`topic_direction`, `topic_axes`). If those
# columns aren't populated, we also look in `brand.metadata` under the same
# keys — a compatibility path for brands created before the columns existed.
_TOPIC_DIRECTION_KEYS = ("topic_direction",)
_TOPIC_AXES_KEYS = ("topic_axes",)


def _read_topic_config(brand) -> tuple[Optional[str], Optional[dict[str, str]]]:
    """Read the brand's direction and axes.

    Returns (direction, axes). Either may be None.

    Sources, in order of precedence for each field:
      1. First-class column on the brand (topic_direction / topic_axes)
      2. Same key inside brand.metadata (jsonb compatibility path)

    The brand object may be a Pydantic model, a dataclass, or a Supabase row
    wrapper. Every read is done defensively via getattr and .get(...).
    """
    if brand is None:
        return None, None

    metadata = getattr(brand, "metadata", None)
    if not isinstance(metadata, dict):
        metadata = {}

    direction: Optional[str] = None
    for key in _TOPIC_DIRECTION_KEYS:
        value = getattr(brand, key, None)
        if isinstance(value, str) and value.strip():
            direction = value.strip()
            break
    if direction is None:
        raw = metadata.get("topic_direction")
        if isinstance(raw, str) and raw.strip():
            direction = raw.strip()

    axes: Optional[dict[str, str]] = None
    for key in _TOPIC_AXES_KEYS:
        value = getattr(brand, key, None)
        if isinstance(value, dict) and value:
            axes = {str(k): str(v) for k, v in value.items() if v}
            break
    if axes is None:
        raw = metadata.get("topic_axes")
        if isinstance(raw, dict) and raw:
            axes = {str(k): str(v) for k, v in raw.items() if v}

    return direction, axes


class TopicSelector:
    """
    Selects topics for content generation.

    Priority order:
      1. Direction-based AI generation (brand.topic_direction set, AI fabric up)
      2. Brand's custom_topics (if configured in brand settings)
      3. brand_config.fallback_topics + keywords (Supabase)
      4. Brand's Knowledge Base entries (dynamically queried)
      5. Platform fallback topics
      6. Unconfigured sentinel (single honest message; callers may skip)

    The direction path is opt-in: it runs when the brand has a
    topic_direction, and returns None on any failure so the caller can fall
    through to selection.

    Tier 6 returns a single message and the sentinel category
    ``UNCONFIGURED_CATEGORY``. Callers that want to refuse publishing an
    unconfigured brand can check for that category. The pipeline currently
    publishes whatever this method returns, so the sentinel is a signal,
    not an enforcement.
    """

    def __init__(
        self,
        brand=None,
        categories: Optional[dict[str, list[str]]] = None,
        knowledge_repo=None,
    ) -> None:
        import threading

        self.brand = brand
        self._lock = threading.Lock()
        self._categories: dict[str, list[str]] = {}
        self._unused: list[str] = []

        # Direction fields, resolved once at construction. Used by
        # generate_from_direction() and by the caller's mode decision.
        self._topic_direction, self._topic_axes = _read_topic_config(brand)
        if self._topic_direction:
            logger.info(
                "TopicSelector: Direction-based generation available for brand '%s' "
                "(%d axes)",
                getattr(brand, "name", "unknown"),
                len(self._topic_axes or {}),
            )

        # 1. Explicit categories passed in
        if categories is not None:
            self._categories = categories
            return

        # 2. Brand has custom_topics configured
        if brand and getattr(brand, 'custom_topics', None):
            if isinstance(brand.custom_topics, dict) and brand.custom_topics:
                self._categories = brand.custom_topics
                logger.info(
                    "TopicSelector: Using custom_topics for brand '%s' (%d categories)",
                    brand.name, len(self._categories),
                )
                return

        # 3. brand_config.fallback_topics / keywords (Supabase via runtime_config)
        bc = _categories_from_brand_config(brand)
        if bc:
            self._categories = bc
            logger.info(
                "TopicSelector: Using brand_config topics for brand '%s' (%d categories)",
                getattr(brand, "name", "unknown") if brand else "default",
                len(self._categories),
            )
            return

        # 4. Query Knowledge Base for this brand
        kb_categories = _build_categories_from_knowledge_base(brand, knowledge_repo)
        if kb_categories:
            self._categories = kb_categories
            return

        # 5. Platform safe defaults (avoid empty selector / generic only path)
        try:
            from core.brand_content_config import get_fallback_topics
            brand_id = str(getattr(brand, "id", None) or getattr(brand, "name", None) or "default")
            ws = str(getattr(brand, "workspace_id", None) or "default")
            topics = get_fallback_topics(brand_id, ws)
            if topics:
                self._categories = {"General": list(topics)}
                logger.info(
                    "TopicSelector: Using platform fallback topics for brand '%s' (%d topics)",
                    getattr(brand, "name", brand_id),
                    len(topics),
                )
                return
        except Exception as fb_exc:
            logger.debug("TopicSelector platform fallback failed: %s", fb_exc)

        # 6. Unconfigured sentinel. This is not a topic list — it is a signal
        # that the brand has not been set up. Log loudly so it shows up in
        # fleet diagnostics, and mark the category so callers can skip.
        logger.error(
            "TopicSelector: Brand '%s' (id=%s) has no configured topic source. "
            "Add one of: topic_direction, custom_topics, "
            "brand_config.fallback_topics, or Knowledge Base entries. "
            "Returning unconfigured sentinel.",
            getattr(brand, 'name', 'unknown') if brand else 'default',
            getattr(brand, 'id', None) if brand else None,
        )
        self._categories = {UNCONFIGURED_CATEGORY: list(_UNCONFIGURED_TOPICS)}

    # ------------------------------------------------------------------
    # Direction-based generation
    # ------------------------------------------------------------------

    @property
    def has_direction(self) -> bool:
        """True when the brand has a topic_direction set."""
        return bool(self._topic_direction)

    def generate_from_direction(
        self,
        *,
        recent: Optional[list[str]] = None,
        temperature: float = 0.9,
    ) -> Optional[str]:
        """Generate one topic from the brand's direction using the AI fabric.

        Returns None when:
          - the brand has no topic_direction
          - the AI fabric is unavailable
          - the AI returns no usable text

        Callers should fall through to selection (pick()) when this returns
        None. The method never raises.
        """
        direction = self._topic_direction
        if not direction:
            return None

        try:
            from ai.gateway import get_llm_gateway
        except Exception as exc:
            logger.debug("Direction generation: gateway import failed: %s", exc)
            return None

        axes = self._topic_axes or {}
        axes_block = ""
        if axes:
            axes_lines = "\n".join(
                f"- {name}: {desc}" for name, desc in axes.items()
            )
            axes_block = (
                "Thematic axes this brand draws from:\n"
                f"{axes_lines}\n\n"
                "Choose ONE axis at random, then generate a topic under it.\n\n"
            )

        recent_block = ""
        if recent:
            recent_slice = [str(t).strip() for t in recent[-10:] if str(t).strip()]
            if recent_slice:
                recent_block = (
                    "Recent topics this brand has already used:\n"
                    + "\n".join(f"- {t}" for t in recent_slice)
                    + "\n\nDo NOT duplicate any of the above.\n\n"
                )

        prompt = (
            f"Brand voice and direction:\n{direction}\n\n"
            f"{axes_block}"
            f"{recent_block}"
            f"Generate ONE short topic (3-8 words) that fits this brand. "
            f"Be specific and evocative. Respond with only the topic, "
            f"no quotes, no explanation, no trailing punctuation."
        )

        try:
            gateway = get_llm_gateway()
            result = gateway.complete(
                prompt,
                capability="chat",
                temperature=temperature,
                max_tokens=40,
                caller="content",
            )
        except Exception as exc:
            logger.warning("Direction generation: gateway call failed: %s", exc)
            return None

        if not getattr(result, "ok", False):
            logger.info(
                "Direction generation: gateway declined (mode=%s reason=%s)",
                getattr(result, "mode", "unknown"),
                getattr(result, "reason", "")[:120],
            )
            return None

        text = str(getattr(result, "text", "") or "").strip()
        if not text:
            return None

        # The model was instructed to return only the topic; strip any
        # accidental quoting/punctuation and bound the length.
        topic = text.strip().strip('"').strip("'").rstrip(".").strip()
        topic = topic.replace("\n", " ").strip()
        if len(topic) > 120:
            topic = topic[:120].rstrip()

        if not topic:
            return None

        logger.info(
            "TopicSelector: Direction-generated topic='%s' provider=%s model=%s",
            topic[:80],
            getattr(result, "provider", "unknown"),
            getattr(result, "model", "unknown"),
        )
        return topic

    # ------------------------------------------------------------------
    # Selection (existing behavior, unchanged)
    # ------------------------------------------------------------------

    def pick(self, category: Optional[str] = None) -> tuple[str, str]:
        """Return (topic, category). Returns the unconfigured sentinel when
        the brand has no topic source at all."""
        with self._lock:
            if not self._categories:
                logger.error(
                    "TopicSelector: pick() called on an empty selector. "
                    "This indicates the constructor was bypassed."
                )
                return (_UNCONFIGURED_TOPICS[0], UNCONFIGURED_CATEGORY)

            if category and category in self._categories:
                cat = category
            else:
                cat = self._next_category_locked()

            topics = self._categories.get(cat, [])
            if not topics:
                return (_UNCONFIGURED_TOPICS[0], UNCONFIGURED_CATEGORY)

            topic = random.choice(topics)
            return topic, cat

    def _next_category_locked(self) -> str:
        if not self._unused:
            self._unused = list(self._categories.keys())
            random.shuffle(self._unused)
        return self._unused.pop()

    def has_topics(self) -> bool:
        """Check if this selector has any topics available."""
        return len(self._categories) > 0

    def is_configured(self) -> bool:
        """True when the selector has a real topic source, not the sentinel."""
        return (
            bool(self._categories)
            and UNCONFIGURED_CATEGORY not in self._categories
        )

    def all_categories(self) -> list[str]:
        return list(self._categories.keys())

    def topics_for(self, category: str) -> list[str]:
        return list(self._categories.get(category, []))

    def total_topics(self) -> int:
        return sum(len(v) for v in self._categories.values())


topic_selector = TopicSelector()


__all__ = [
    "TopicSelector",
    "topic_selector",
    "UNCONFIGURED_CATEGORY",
]
