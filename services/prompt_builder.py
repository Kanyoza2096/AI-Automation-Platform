"""
Prompt Builder Service.
Assembles complete LLM prompts using Brand and AIProfile contexts.

V1.9 (2026-10-02) — PLAIN-TEXT OUTPUT CONTRACT:
  - POST_SCHEMA_PROMPT_BLOCK now forbids Markdown syntax. Captions are
    published to platforms (Facebook, Instagram, LinkedIn text) that
    render plain text; Markdown control characters (**bold**, ## heading,
    * bullet) leak through as literal characters. The block now
    instructs the model to use plain text with "•" for lists and
    sentence structure for emphasis.
  - The prompt's "structure beautifully" phrasing (which in training
    data signals Markdown) is replaced with concrete structural
    guidance that names the forbidden syntax.
  - Added an anti-truncation directive: the caption must end at a
    sentence boundary. Without this, models that hit their token
    ceiling mid-sentence produce visibly truncated posts.
  - The topic fallback no longer says "please select an engaging topic"
    (which invited the model to invent one and produce duplicate
    content); it now says the topic will be supplied by the caller,
    failing closed if absent.
  - The prose path (structured=False) also gets a no-Markdown block;
    the model produces Markdown by default regardless of capability.

V1.8 (2026-10-01) — STRUCTURED-OUTPUT SUPPORT (preserved):
  - build_post_prompt accepts `structured: bool = False`.
  - POST_SCHEMA_REQUIRED_KEYS is the single source of truth for the
    parser contract.

V1.7 (2026-09-28) — PERMANENT DESIGN (preserved):
  - Style guides loaded from config/content_styles.yaml (data, not code).
  - Brand.style_category provides explicit override.
  - Brand.custom_style_guide provides full per-brand override.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, List, Dict, Any, Mapping

import yaml

from models.brand import Brand
from models.ai_profile import AIProfile

logger = logging.getLogger("kanyoza.services.prompt_builder")


# ═══════════════════════════════════════════════════════════════════════════
# Structured-output contract — SINGLE SOURCE OF TRUTH
# ═══════════════════════════════════════════════════════════════════════════
#
# The prompt tells the model what to emit. The parser (models.content)
# accepts these keys (plus a documented alias set for drift tolerance).
# The gateway's complete_structured() enforces POST_SCHEMA_REQUIRED_KEYS
# at the boundary — if the model omits one, the call is rejected before
# the content engine ever sees it.
#
# The caption is published to platforms that render plain text. Any
# Markdown the model emits (**bold**, ## heading, * bullet, `code`) is
# shown to readers as literal characters. The block below forbids
# Markdown explicitly. A companion sanitizer (sanitize_caption_markdown)
# runs at the publish boundary as defense-in-depth for model drift.
#
POST_SCHEMA_REQUIRED_KEYS: tuple[str, ...] = ("title", "caption")

POST_SCHEMA_PROMPT_BLOCK = """
OUTPUT FORMAT — CRITICAL, DO NOT DEVIATE:
Respond with a SINGLE JSON object. No prose before or after. No markdown fences.
Do not explain your reasoning. Return raw JSON and nothing else.

Required keys (both MUST be non-empty strings):
{
  "title":   "4-15 word headline of the post, plain text, no Markdown",
  "caption": "the full body of the post, 150-400 words, plain text, no Markdown"
}

Optional keys (omit if not applicable; do not return empty strings):
{
  "hashtags": ["strings", "without", "the", "hash", "prefix"],
  "theme":    "one of: comparison, tutorial, checklist, architecture, code_tips, ai, security, database, backend, frontend, system_design, cloud"
}

PLAIN-TEXT RULES — the caption is published to a plain-text platform.
Any of the following syntax will be shown to readers as literal
characters, which is ugly and unprofessional:
- Do NOT use ** or __ for bold.
- Do NOT use * or _ for italic.
- Do NOT use ##, ###, or # for headings.
- Do NOT use backticks ` for code.
- Do NOT use > for blockquotes.
- Do NOT use - or * at the start of lines for list items.
- For a list, use the bullet character "•" followed by a space.
- For emphasis, use sentence structure, word order, or a leading
  capital — not asterisks.
- For line breaks, use "\\n". Blank lines between paragraphs are fine.

COMPLETENESS RULE:
- The caption MUST end at a sentence boundary (period, question mark,
  or exclamation point). Do not end mid-sentence, mid-word, or with a
  trailing ellipsis. If you approach your length budget, finish the
  current sentence and stop. A shorter complete caption is always
  better than a longer truncated one.

EDITORIAL RULES:
- Do NOT begin with "In today's fast-paced...", "In the world of...",
  or any similar throat-clearing phrase. Start with the substantive
  claim.
- Do NOT end with a "Takeaway", "Summary", "TL;DR", or "Key insight"
  section. Close with a specific observation or a question directed
  at the reader.
- Do NOT include any text before or after the JSON object.

Hard rules:
- "title" and "caption" MUST both be present and non-empty.
- Return raw JSON, not a Markdown code block.
- Do not add commentary outside the JSON object.
""".strip()


# Prose path (structured=False) also needs a plain-text directive.
# Without it, the model produces Markdown by default.
_PROSE_PLAIN_TEXT_BLOCK = """
PLAIN-TEXT RULES — the post is published to a plain-text platform:
- Do NOT use Markdown syntax. No **, __, *, _, ##, `, or >.
- For lists, use the bullet character "•" followed by a space.
- For line breaks, use actual line breaks. Blank lines between
  paragraphs are fine.
- For emphasis, use sentence structure — not asterisks.
""".strip()


# ═══════════════════════════════════════════════════════════════════════════
# Style Registry — loads once at import, hot-swappable via reload()
# ═══════════════════════════════════════════════════════════════════════════

_STYLE_REGISTRY_PATH = Path(__file__).parent.parent / "config" / "content_styles.yaml"

_SAFE_FALLBACK_GUIDE = """
Content Style: GENERAL — CONSERVATIVE
- Write a clear, engaging social media post.
- Match the tone suggested by the audience description above.
- Use plain language. Avoid technical jargon unless the topic itself is technical.
- Avoid corporate verbs such as "leverage", "deploy", "optimize", "scale", "ship".
- Avoid metaphors borrowed from software engineering, systems architecture,
  or business operations unless the topic itself is about those fields.
- Prefer specific, concrete language over abstract comparisons.
- End with a clear call to action and hashtags.
"""


class StyleRegistry:
    """Loads and serves content style guides from YAML."""

    def __init__(self, path: Path = _STYLE_REGISTRY_PATH) -> None:
        self._path = path
        self._styles: Dict[str, Dict[str, Any]] = {}
        self._version: int = 0
        self.reload()

    def reload(self) -> None:
        try:
            if not self._path.exists():
                logger.warning("Style registry not found at %s", self._path)
                self._styles = {}
                return
            with self._path.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            styles = data.get("styles") or {}
            if not isinstance(styles, dict):
                logger.warning("Style registry 'styles' is not a dict")
                styles = {}
            self._styles = styles
            self._version = int(data.get("version", 0))
            logger.info(
                "Style registry loaded: %d styles (version=%d) from %s",
                len(self._styles), self._version, self._path,
            )
        except Exception as exc:
            logger.exception("Style registry reload failed: %s", exc)
            self._styles = {}

    @property
    def version(self) -> int:
        return self._version

    def all_names(self) -> List[str]:
        return sorted(self._styles.keys())

    def get(self, name: str) -> Optional[Dict[str, Any]]:
        if not name:
            return None
        return self._styles.get(name.strip().lower())

    def guide_for(self, name: str) -> Optional[str]:
        entry = self.get(name)
        if entry and isinstance(entry, Mapping):
            guide = entry.get("guide")
            if isinstance(guide, str) and guide.strip():
                return guide.strip()
        return None

    def infer_style_for(self, brand: "Brand") -> str:
        explicit = getattr(brand, "style_category", None)
        if explicit and self.get(explicit):
            return explicit.strip().lower()

        tone = (getattr(brand, "tone", "") or "").lower()
        audience = (getattr(brand, "audience_description", "") or "").lower()
        name = (getattr(brand, "name", "") or "").lower()
        slug = (getattr(brand, "slug", "") or "").lower()

        best_key: Optional[str] = None
        best_score = 0.0

        for key, entry in self._styles.items():
            if not isinstance(entry, Mapping):
                continue
            match = entry.get("match") or {}
            score = 0.0
            for signal in (match.get("tones") or []):
                if signal and signal in tone:
                    score += 3.0
            for signal in (match.get("audience_signals") or []):
                if signal and signal in audience:
                    score += 2.0
            for signal in (match.get("name_signals") or []):
                if signal and (signal in name or signal in slug):
                    score += 2.0
            if score > best_score:
                best_score = score
                best_key = key

        if best_key and best_score > 0:
            return best_key
        return "general"

    def resolve_for_brand(self, brand: "Brand") -> tuple[str, str]:
        """Return (guide, source_label).

        source_label is one of: 'custom', 'registry:<key>', 'fallback'.
        Callers log this so the log statement matches what was actually used.
        """
        custom = getattr(brand, "custom_style_guide", None)
        if isinstance(custom, str) and custom.strip():
            return custom.strip(), "custom"

        key = self.infer_style_for(brand)
        guide = self.guide_for(key)
        if guide:
            return guide, f"registry:{key}"
        return _SAFE_FALLBACK_GUIDE.strip(), "fallback"

    def guide_for_brand(self, brand: "Brand") -> str:
        """Backward-compatible: returns just the guide text."""
        guide, _ = self.resolve_for_brand(brand)
        return guide


_style_registry = StyleRegistry()


def get_style_registry() -> StyleRegistry:
    return _style_registry


def reload_style_registry() -> None:
    _style_registry.reload()


# ═══════════════════════════════════════════════════════════════════════════
# PromptBuilder
# ═══════════════════════════════════════════════════════════════════════════

class PromptBuilder:
    """Service responsible for generating context-aware prompts."""

    service_name = "prompt_builder"

    def __init__(self, style_registry: Optional[StyleRegistry] = None):
        self._registry = style_registry or _style_registry

    def health_check(self) -> dict:
        try:
            test_prompt = self.build_post_prompt(topic="Test topic")
            styles = self._registry.all_names()
            return {
                "status": "healthy",
                "service": self.service_name,
                "message": "Prompt builder is operational",
                "can_build_prompt": bool(test_prompt),
                "style_registry_version": self._registry.version,
                "styles_available": len(styles),
                "styles": styles,
            }
        except Exception as exc:
            return {
                "status": "unknown",
                "service": self.service_name,
                "error": str(exc)[:200],
                "message": "Prompt builder is unavailable",
            }

    def get_status(self) -> dict:
        return self.health_check()

    # ── Defaults ──────────────────────────────────────────────────────

    @staticmethod
    def _default_brand() -> "Brand":
        from models.brand import Brand as _Brand
        return _Brand.create_default("default")

    @staticmethod
    def _default_ai_profile() -> "AIProfile":
        from models.ai_profile import AIProfile as _AIProfile
        return _AIProfile.create_default("default")

    # ── Public API ────────────────────────────────────────────────────

    def build_post_prompt(
        self,
        brand: Optional["Brand"] = None,
        ai_profile: Optional["AIProfile"] = None,
        topic: Optional[str] = None,
        knowledge_context: Optional[str] = None,
        structured: bool = False,
    ) -> str:
        """Build a post-generation prompt.

        `structured=True` appends POST_SCHEMA_PROMPT_BLOCK so the model
        emits a JSON object matching POST_SCHEMA_REQUIRED_KEYS. Callers
        using the gateway's structured path MUST set this. Callers
        generating free prose (chat, fallback) MUST NOT — the prose
        path uses _PROSE_PLAIN_TEXT_BLOCK instead, which forbids
        Markdown without requesting a JSON shape.
        """
        if brand is None:
            brand = self._default_brand()
        if ai_profile is None:
            ai_profile = self._default_ai_profile()

        system_prompt = ai_profile.build_system_prompt(
            brand_name=brand.name,
            audience=brand.audience_description,
        )

        brand_context = f"Brand Tone: {brand.tone}\n"
        brand_context += f"Brand Language: {brand.language}\n"
        if getattr(brand, "hashtags", None):
            brand_context += f"Default Hashtags: {brand.formatted_hashtags}\n"
        style_category = getattr(brand, "style_category", None)
        if style_category:
            brand_context += f"Style Category: {style_category}\n"

        # Topic: supplied by the pipeline. If absent, fail loud rather than
        # inviting the model to invent one (which produced duplicate content
        # in the previous version).
        topic_str = (
            f"Topic: {topic}"
            if topic and str(topic).strip()
            else "Topic: (none supplied by the caller — respond with an error marker if you cannot proceed without one)"
        )

        knowledge_str = (
            f"\nUse the following knowledge base facts if relevant:\n{knowledge_context}\n"
            if knowledge_context
            else ""
        )

        style_guide, style_source = self._registry.resolve_for_brand(brand)

        # Structured path gets the full schema block (which already forbids
        # Markdown). Prose path gets the plain-text-only block.
        if structured:
            format_instruction = f"\n{POST_SCHEMA_PROMPT_BLOCK}\n"
        else:
            format_instruction = f"\n{_PROSE_PLAIN_TEXT_BLOCK}\n"

        task = f"""
Task:
Write a highly engaging social media post for {brand.name}.
{topic_str}

{style_guide}

Guidelines:
1. Write in the exact tone and style of the persona defined above.
2. Structure the content with short paragraphs and, if a list helps,
   a bulleted list using the "•" character. Do NOT use Markdown syntax.
3. Match the emoji density appropriate to the brand persona — use
   emoji sparingly, as punctuation, not as decoration.
4. Include a clear call to action (CTA) matching the '{ai_profile.cta_style}' style.
5. End with appropriate hashtags.
{format_instruction}
{brand_context}
{knowledge_str}
"""
        logger.debug(
            "Built post prompt: brand=%s style=%s registry_version=%s structured=%s",
            brand.name, style_source, self._registry.version, structured,
        )
        return f"{system_prompt}\n\n{task}"

    def build_chat_prompt(
        self,
        brand: Optional["Brand"] = None,
        ai_profile: Optional["AIProfile"] = None,
        user_profile: Optional[Dict[str, Any]] = None,
        conversation_history: Optional[List[Dict[str, str]]] = None,
    ) -> str:
        if brand is None:
            brand = self._default_brand()
        if ai_profile is None:
            ai_profile = self._default_ai_profile()
        if user_profile is None:
            user_profile = {}

        system_prompt = ai_profile.build_system_prompt(
            brand_name=brand.name,
            audience=brand.audience_description,
        )

        user_name = user_profile.get("name", "User")

        chat_context = f"""
Task Context:
You are conversing directly with {user_name} in a chat interface.
Brand Language: {brand.language}

Guidelines:
1. Respond naturally, concisely, and consistently with your persona.
2. Keep responses brief since this is a chat interface.
3. Address the user's questions or comments thoughtfully.
4. Do NOT sound like a generic AI assistant. Maintain the {ai_profile.name} identity.
5. Do NOT use Markdown formatting. This is a plain-text chat surface.
"""
        return f"{system_prompt}\n\n{chat_context}"


prompt_builder = PromptBuilder()


__all__ = [
    "PromptBuilder",
    "prompt_builder",
    "StyleRegistry",
    "get_style_registry",
    "reload_style_registry",
    "POST_SCHEMA_REQUIRED_KEYS",
    "POST_SCHEMA_PROMPT_BLOCK",
]
