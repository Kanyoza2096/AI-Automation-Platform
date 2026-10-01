"""
Prompt Builder Service.
Assembles complete LLM prompts using Brand and AIProfile contexts.

V1.8 (2026-10-01) — STRUCTURED-OUTPUT SUPPORT:
  - build_post_prompt accepts `structured: bool = False`.
  - When True, appends POST_SCHEMA_PROMPT_BLOCK to the task.
  - POST_SCHEMA_REQUIRED_KEYS is the single source of truth for the
    parser contract; models.content.TechPost.from_gemini_response MUST
    accept at least these keys.
  - Structured callers (ContentEngine._generate_structured_content) MUST
    pass structured=True. Prose callers (chat, unstructured fallback)
    MUST NOT, or the model emits JSON where prose is expected.

V1.7 (2026-09-28) — PERMANENT DESIGN (preserved):
  - Style guides loaded from config/content_styles.yaml (data, not code).
  - Brand.style_category provides explicit override.
  - Brand.custom_style_guide provides full per-brand override.
  - Tone/audience/name inference against the registry for fallback.
  - Adding a new vertical = add YAML entry, no code change.
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
POST_SCHEMA_REQUIRED_KEYS: tuple[str, ...] = ("title", "caption")

POST_SCHEMA_PROMPT_BLOCK = """
OUTPUT FORMAT — CRITICAL, DO NOT DEVIATE:
Respond with a SINGLE JSON object. No prose before or after. No markdown fences.

Required keys (both MUST be non-empty strings):
{
  "title":   "4-15 word headline of the post",
  "caption": "the full body of the post, 80-400 words, use \\n for line breaks"
}

Optional keys (omit if not applicable; do not return empty strings):
{
  "hashtags": ["strings", "without", "the", "hash", "prefix"],
  "theme":    "one of: comparison, tutorial, checklist, architecture, code_tips, ai, security, database, backend, frontend, system_design, cloud"
}

Hard rules:
- "title" and "caption" MUST both be present and non-empty.
- Return raw JSON, not a Markdown code block.
- Do not add commentary outside the JSON object.
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
        generating free prose (chat, fallback) MUST NOT.
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

        topic_str = (
            f"Topic: {topic}"
            if topic
            else "Topic: Please select an engaging and highly relevant topic for the audience."
        )

        knowledge_str = (
            f"\nUse the following knowledge base facts if relevant:\n{knowledge_context}\n"
            if knowledge_context
            else ""
        )

        style_guide, style_source = self._registry.resolve_for_brand(brand)

        schema_instruction = f"\n{POST_SCHEMA_PROMPT_BLOCK}\n" if structured else ""

        task = f"""
Task:
Write a highly engaging social media post for {brand.name}.
{topic_str}

{style_guide}

Guidelines:
1. Write in the exact tone and style of the persona defined above.
2. Structure the content beautifully (paragraphs, lists if necessary).
3. Follow the requested emoji density strictly.
4. Include a clear call to action (CTA) matching the '{ai_profile.cta_style}' style.
5. End with appropriate hashtags.
{schema_instruction}
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
