"""
Prompt Builder Service.
Assembles complete LLM prompts using Brand and AIProfile contexts.

V1.7 (2026-09-28) — PERMANENT DESIGN:
  - Style guides loaded from config/content_styles.yaml (data, not code).
  - Brand.style_category provides explicit override.
  - Brand.custom_style_guide provides full per-brand override.
  - Tone/audience/name inference against the registry for fallback.
  - Adding a new vertical = add YAML entry, no code change.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional, List, Dict, Any, Mapping

import yaml

from models.brand import Brand
from models.ai_profile import AIProfile

logger = logging.getLogger("kanyoza.services.prompt_builder")


# ═══════════════════════════════════════════════════════════════════════════
# Style Registry — loads once at import, hot-swappable via reload()
# ═══════════════════════════════════════════════════════════════════════════

_STYLE_REGISTRY_PATH = Path(__file__).parent.parent / "config" / "content_styles.yaml"

# Conservative fallback used if the YAML is missing or unreadable.
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
        """Reload styles from disk. Safe to call at runtime."""
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
                len(self._styles),
                self._version,
                self._path,
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
        """
        Infer the best-matching style key from brand signals.

        Preference order:
          1. brand.style_category (explicit)
          2. Score-based match against tone + audience + name
          3. "general" (falls through to SAFE_FALLBACK)
        """
        # 1) Explicit category
        explicit = getattr(brand, "style_category", None)
        if explicit and self.get(explicit):
            return explicit.strip().lower()

        # 2) Score-based inference
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

    def guide_for_brand(self, brand: "Brand") -> str:
        """
        Resolve the style guide to inject into the prompt.

        Priority:
          1. brand.custom_style_guide (full per-brand override)
          2. style registry entry (from style_category or inference)
          3. SAFE_FALLBACK_GUIDE
        """
        custom = getattr(brand, "custom_style_guide", None)
        if isinstance(custom, str) and custom.strip():
            return custom.strip()

        key = self.infer_style_for(brand)
        guide = self.guide_for(key)
        if guide:
            return guide

        return _SAFE_FALLBACK_GUIDE.strip()


# Module singleton
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
        """Health check for Meta Engine monitoring."""
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
        knowledge_context: Optional[str] = None
    ) -> str:
        """Builds a comprehensive prompt for generating a social media post.

        The style guide is resolved via the style registry:
          - brand.custom_style_guide (full override)
          - brand.style_category (explicit key)
          - tone/audience/name inference (fallback)
          - conservative general fallback (last resort)
        """
        if brand is None:
            brand = self._default_brand()
        if ai_profile is None:
            ai_profile = self._default_ai_profile()

        # System persona from AI profile
        system_prompt = ai_profile.build_system_prompt(
            brand_name=brand.name,
            audience=brand.audience_description,
        )

        # Brand context
        brand_context = f"Brand Tone: {brand.tone}\n"
        brand_context += f"Brand Language: {brand.language}\n"
        if getattr(brand, "hashtags", None):
            brand_context += f"Default Hashtags: {brand.formatted_hashtags}\n"
        style_category = getattr(brand, "style_category", None)
        if style_category:
            brand_context += f"Style Category: {style_category}\n"

        # Topic
        topic_str = (
            f"Topic: {topic}"
            if topic
            else "Topic: Please select an engaging and highly relevant topic for the audience."
        )

        # Knowledge
        knowledge_str = (
            f"\nUse the following knowledge base facts if relevant:\n{knowledge_context}\n"
            if knowledge_context
            else ""
        )

        # ✅ Style guide resolved from registry — no if/elif
        style_guide = self._registry.guide_for_brand(brand)
        style_name = self._registry.infer_style_for(brand)

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

{brand_context}
{knowledge_str}
"""
        logger.debug(
            "Built post prompt: brand=%s style=%s registry_version=%s",
            brand.name,
            style_name,
            self._registry.version,
        )
        return f"{system_prompt}\n\n{task}"

    def build_chat_prompt(
        self,
        brand: Optional["Brand"] = None,
        ai_profile: Optional["AIProfile"] = None,
        user_profile: Optional[Dict[str, Any]] = None,
        conversation_history: Optional[List[Dict[str, str]]] = None,
    ) -> str:
        """Builds a prompt for conversational chat (Messenger, etc.)."""
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


# Singleton
prompt_builder = PromptBuilder()


__all__ = [
    "PromptBuilder",
    "prompt_builder",
    "StyleRegistry",
    "get_style_registry",
    "reload_style_registry",
]
