from __future__ import annotations

import random
import logging
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from models.brand import Brand

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Sentinel category for the unconfigured fallback
# ═══════════════════════════════════════════════════════════════════════════
#
# When a brand has no custom_topics, no brand_config, no KB entries, and no
# platform fallback, TopicSelector returns a topic from the last-resort list.
# Callers that want to skip unconfigured brands can check
# `category == UNCONFIGURED_CATEGORY` and refuse to publish.
#
UNCONFIGURED_CATEGORY = "unconfigured"

_UNCONFIGURED_TOPICS: list[str] = [
    "This brand has no topic source configured; add custom_topics, "
    "brand_config.fallback_topics, or Knowledge Base entries.",
]


# ═══════════════════════════════════════════════════════════════════════════
# V3.0 — DIRECTION FEATURE REMOVED
# ═══════════════════════════════════════════════════════════════════════════
#
# The direction-based topic generation feature (brand.topic_direction read
# by the model to invent topics) was removed in v3.0.
#
# Why: direction generation produced abstract topics from the brand charter
# that the corpus could not support. Retrieval returned irrelevant chunks
# because no record matched the topic. The prompt's quality contract
# demanded concrete evidence, so the model invented metrics to fill the
# gap. Result: fabricated engineering posts (e.g., "our auth latency was
# 250ms until we switched to JWT").
#
# The fix: topics come exclusively from the brand's Knowledge Base (or
# custom_topics, or brand_config.fallback_topics). Every topic is backed
# by at least one record, so retrieval is grounded and the model has
# nothing to invent.
#
# brands.topic_direction column is retained in the schema for backward
# compatibility with existing brand rows, but its value is no longer read
# by TopicSelector. Retire the column in a future migration once all brand
# rows have been reviewed and the frontend no longer writes to it.
# ═══════════════════════════════════════════════════════════════════════════


def _build_categories_from_knowledge_base(
    brand, knowledge_repo=None
) -> Optional[dict[str, list[str]]]:
    """Query the brand's Knowledge Base and build topics from it.

    Reads `metadata.title` and `metadata.theme` from each entry — the
    fields the bulk ingestion writes. Falls back to `entry.title` and
    content-derived topics for legacy records. Categories are grouped
    by theme; topics within each category are the entry titles.
    """
    if not brand or not getattr(brand, 'id', None):
        return None
    if knowledge_repo is None:
        return None

    try:
        workspace_id = getattr(brand, "workspace_id", None)
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            logger.warning(
                "TopicSelector: refusing unscoped KB topic lookup for brand '%s'",
                getattr(brand, "name", "unknown"),
            )
            return None
        entries = knowledge_repo.find_by_brand(brand.id, workspace_id)
        if not entries:
            logger.info(
                "TopicSelector: No KB entries for brand '%s' (id=%s)",
                getattr(brand, 'name', 'unknown'), brand.id,
            )
            return None

        kb_categories: dict[str, list[str]] = {}
        for entry in entries:
            metadata = getattr(entry, 'metadata', None) or {}
            theme = str(metadata.get('theme') or '').strip()
            meta_title = str(metadata.get('title') or '').strip()

            if meta_title:
                topic = meta_title
            elif getattr(entry, 'title', None) and entry.title.strip():
                topic = entry.title.strip()
            elif getattr(entry, 'content', None) and entry.content.strip():
                first_sentence = entry.content.strip().split('.')[0].strip()
                topic = first_sentence[:100] if len(first_sentence) > 100 else first_sentence
            else:
                continue

            if theme:
                cat_display = theme.replace('_', ' ').title()
            else:
                cat = getattr(entry, 'doc_type', None) or 'general'
                cat_display = cat.replace('_', ' ').title()

            if cat_display not in kb_categories:
                kb_categories[cat_display] = []
            if topic not in kb_categories[cat_display]:
                kb_categories[cat_display].append(topic)

        # "X vs Y" comparison topics within a category
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
    """Load fallback_topics and keywords from brand_config table."""
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


class TopicSelector:
    """Selects topics for content generation from configured sources.

    Priority order:
      1. Brand's custom_topics (if configured in brand settings)
      2. brand_config.fallback_topics + keywords (Supabase)
      3. Brand's Knowledge Base entries (dynamically queried)
      4. Platform fallback topics
      5. Unconfigured sentinel (single honest message; callers may skip)

    V3.0 — DIRECTION REMOVED
      The direction-based generation path was removed in v3.0. See the
      module docstring above for the rationale. Topics now come
      exclusively from the KB, custom_topics, or brand_config — never
      from a brand charter interpreted by the model.
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

        if categories is not None:
            self._categories = categories
            return

        if brand and getattr(brand, 'custom_topics', None):
            if isinstance(brand.custom_topics, dict) and brand.custom_topics:
                self._categories = brand.custom_topics
                logger.info(
                    "TopicSelector: Using custom_topics for brand '%s' (%d categories)",
                    brand.name, len(self._categories),
                )
                return

        bc = _categories_from_brand_config(brand)
        if bc:
            self._categories = bc
            logger.info(
                "TopicSelector: Using brand_config topics for brand '%s' (%d categories)",
                getattr(brand, "name", "unknown") if brand else "default",
                len(self._categories),
            )
            return

        kb_categories = _build_categories_from_knowledge_base(brand, knowledge_repo)
        if kb_categories:
            self._categories = kb_categories
            return

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

        logger.error(
            "TopicSelector: Brand '%s' (id=%s) has no configured topic source. "
            "Add one of: custom_topics, brand_config.fallback_topics, or "
            "Knowledge Base entries. Returning unconfigured sentinel.",
            getattr(brand, 'name', 'unknown') if brand else 'default',
            getattr(brand, 'id', None) if brand else None,
        )
        self._categories = {UNCONFIGURED_CATEGORY: list(_UNCONFIGURED_TOPICS)}

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def pick(self, category: Optional[str] = None) -> tuple[str, str]:
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
        return len(self._categories) > 0

    def is_configured(self) -> bool:
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
