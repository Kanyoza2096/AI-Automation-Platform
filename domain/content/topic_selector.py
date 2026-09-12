from __future__ import annotations

import random
import logging
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from models.brand import Brand

logger = logging.getLogger(__name__)


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


class TopicSelector:
    """
    Selects topics for content generation from the brand's Knowledge Base.
    
    Priority order:
      1. Brand's custom_topics (if configured in brand settings)
      2. brand_config.fallback_topics + keywords (Supabase)
      3. Brand's Knowledge Base entries (dynamically queried)
      4. Soft generic pick() message only if still empty
    
    Prefer DB brand_config over hardcoded engine lists.
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

        logger.warning(
            "TopicSelector: No topics available for brand '%s'. "
            "Add brand_config.fallback_topics, KB documents, or custom_topics.",
            getattr(brand, 'name', 'unknown') if brand else 'default',
        )
        # Last resort: non-empty so pick() is not stuck on a single canned line only
        self._categories = {
            "General": [
                "Share a practical tip your audience can use today",
                "Highlight a customer win or lesson learned",
                "Explain one industry trend in plain language",
                "Behind the scenes: how we solve a common problem",
                "A short weekly reflection for the community",
            ]
        }

    def pick(self, category: Optional[str] = None) -> tuple[str, str]:
        """Return (topic, category). Returns (fallback_message, 'general') if no topics."""
        with self._lock:
            if not self._categories:
                return (
                    "Share an inspiring message with our community today",
                    "general",
                )

            if category and category in self._categories:
                cat = category
            else:
                cat = self._next_category_locked()

            topics = self._categories.get(cat, [])
            if not topics:
                return (
                    "Share an inspiring message with our community today",
                    "general",
                )

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

    def all_categories(self) -> list[str]:
        return list(self._categories.keys())

    def topics_for(self, category: str) -> list[str]:
        return list(self._categories.get(category, []))

    def total_topics(self) -> int:
        return sum(len(v) for v in self._categories.values())


topic_selector = TopicSelector()
