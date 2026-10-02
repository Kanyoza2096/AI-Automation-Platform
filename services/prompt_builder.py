"""
Prompt Builder Service.
Assembles complete LLM prompts using Brand and AIProfile contexts.

V2.1 (2026-10-02) — BRAND CHARTER WIRING:
  - brand.topic_direction is now injected into BOTH the post prompt and
    the chat prompt as a "Brand voice and direction" block. Previously
    the direction was only read by TopicSelector.generate_from_direction
    for topic selection; the post-generation and chat paths never saw it.
    The result was that a well-written brand charter ("analytical,
    opinionated, specific; we name tradeoffs; we do not write tutorials,
    listicles, or hype cycles; first-person plural; short paragraphs")
    was used for the topic title and then discarded before the model
    was asked to write the post.
  - The direction block is explicitly marked as overriding any
    conflicting guidance below it. When the brand has a charter, the
    charter wins; the generic rules act as a floor for brands that
    don't.
  - Both build_post_prompt and build_chat_prompt include the block so
    that every generation surface honors the same declared voice.

V2.0 (2026-10-02) — VOICE, SUBSTANCE, AND COMPLETENESS CONTRACT:
  - POST_SCHEMA_PROMPT_BLOCK includes plain-text, completeness, voice,
    and substance rules.
  - _PROSE_PLAIN_TEXT_BLOCK mirrors the same rules for the fallback path.

V1.9 (2026-10-02) — PLAIN-TEXT OUTPUT (preserved).
V1.8 (2026-10-01) — STRUCTURED-OUTPUT SUPPORT (preserved).
V1.7 (2026-09-28) — PERMANENT DESIGN (preserved).
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
# The caption is published to platforms that render plain text. Markdown
# the model emits (**bold**, ## heading, * bullet) arrives as literal
# characters. The block below forbids Markdown. The companion sanitizer
# (sanitize_caption_markdown) runs at the publish boundary as a
# defense-in-depth backstop.
#
POST_SCHEMA_REQUIRED_KEYS: tuple[str, ...] = ("title", "caption")

POST_SCHEMA_PROMPT_BLOCK = """
OUTPUT FORMAT — CRITICAL, DO NOT DEVIATE:
Respond with a SINGLE JSON object. No prose before or after. No markdown
fences. Do not explain your reasoning. Return raw JSON and nothing else.

Required keys (both MUST be non-empty strings):
{
  "title":   "4-15 word headline, plain text, no Markdown",
  "caption": "the full body of the post, 150-400 words, plain text"
}

Optional keys (omit if not applicable; do not return empty strings):
{
  "hashtags": ["strings", "without", "the", "hash", "prefix"],
  "theme":    "one of: comparison, tutorial, checklist, architecture, code_tips, ai, security, database, backend, frontend, system_design, cloud"
}

PLAIN-TEXT RULES — the caption is published to a plain-text platform:
Any of the following syntax will be shown to readers as literal
characters, which is ugly and unprofessional:
- Do NOT use ** or __ for bold.
- Do NOT use * or _ for italic.
- Do NOT use ##, ###, or # for headings.
- Do NOT use backticks ` for code.
- Do NOT use > for blockquotes.
- Do NOT use - or * at the start of lines for list items.
- For a list, use the bullet character "•" followed by a space.
- For line breaks, use "\\n". Blank lines between paragraphs are fine.
- For emphasis, use sentence structure — not asterisks.

COMPLETENESS RULE — this is the single most important rule:
- The caption MUST end at a sentence boundary (period, question mark,
  or exclamation point). Do not end mid-sentence, mid-word, or with a
  trailing ellipsis.
- If you approach your length budget, finish the current sentence and
  stop. A shorter complete caption is always better than a longer
  truncated one.

VOICE — do NOT write like a LinkedIn influencer:
- Do NOT open with "As a [role], I've seen...", "In today's...",
  "We've all been there", or similar throat-clearing phrases.
- Do NOT use "fear not", "friends", "let's dive in", "buckle up".
- Do NOT describe the topic ("Here's a comparison of X and Y").
  Make the point instead.
- Do NOT hedge with "often", "sometimes", "in many cases" when you
  could state the point directly.
- Do NOT close with "The key is...", "The takeaway is...", or a
  summary section that restates what you just said.
- Assume the reader is a developer with 3+ years of experience. Do not
  explain what a CI/CD pipeline is. Do not define terms they use daily.

SUBSTANCE — this is what makes the post worth reading:
- Open with a specific, non-obvious claim. A number, a failure, a
  counter-intuitive observation. Not a restatement of the topic.
- Take a position where the topic allows. "It depends on X, and for
  most teams X is Y" is a position. Presenting two options neutrally
  is not.
- Include at least one concrete detail: a metric, a scenario, a
  specific tool or version.
- Close with a question directed at the reader that invites a real
  reply. Not rhetorical.

Hard rules:
- "title" and "caption" MUST both be present and non-empty.
- Return raw JSON, not a Markdown code block.
- Do not add commentary outside the JSON object.
""".strip()


# Prose path (structured=False) needs the same plain-text, voice, and
# substance rules. Without them, the model defaults to Markdown and to
# the LinkedIn-influencer register that technical audiences reject.
_PROSE_PLAIN_TEXT_BLOCK = """
PLAIN-TEXT RULES — the post is published to a plain-text platform:
- Do NOT use Markdown syntax. No **, __, *, _, ##, `, or >.
- For lists, use the bullet character "•" followed by a space.
- For line breaks, use actual line breaks. Blank lines between
  paragraphs are fine.
- For emphasis, use sentence structure — not asterisks.

COMPLETENESS RULE:
- The caption MUST end at a sentence boundary (period, question mark,
  or exclamation point). Do not end mid-sentence, mid-word, or with
  a trailing ellipsis.
- If you approach your length budget, finish the current sentence and
  stop. A shorter complete caption is always better than a longer
  truncated one.

VOICE — do NOT write like a LinkedIn influencer:
- Do NOT open with "As a [role], I've seen...", "In today's...",
  "We've all been there", or similar throat-clearing phrases.
- Do NOT use "fear not", "friends", "let's dive in", "buckle up".
- Do NOT describe the topic. Make the point instead.
- Do NOT hedge when you could state the point directly.
- Do NOT close with "The key is..." or a summary section.
- Assume the reader is a developer with 3+ years of experience.

SUBSTANCE:
- Open with a specific, non-obvious claim. Not a restatement.
- Take a position where the topic allows.
- Include at least one concrete detail — a number, a scenario, a
  specific tool or version.
- Close with a question directed at the reader that invites a real
  reply.
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

    # ── Brand charter helper ──────────────────────────────────────────

    @staticmethod
    def _brand_charter_block(brand: "Brand") -> str:
        """Return a formatted brand voice/direction block, or "".

        brand.topic_direction is a charter: subject matter, voice, and
        constraints. It was originally written for topic selection but
        reads as an editorial brief. Surfacing it in the post and chat
        prompts means the generation call honors the same declared voice
        the topic-selection call was already following.

        The block is explicitly marked as overriding any conflicting
        guidance below it so that a specific charter beats a generic
        rule. When the brand has no charter, the block is empty and the
        generic rules act as the floor.
        """
        direction = getattr(brand, "topic_direction", None)
        if not isinstance(direction, str) or not direction.strip():
            return ""
        return (
            "\nBrand voice and direction — this OVERRIDES any conflicting "
            "guidance below. Follow it exactly:\n"
            f"{direction.strip()}\n"
        )

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
        path uses _PROSE_PLAIN_TEXT_BLOCK instead, which carries the
        same voice, substance, and completeness rules without
        requesting a JSON shape.
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

        # Topic: supplied by the pipeline. If absent, do not invite the
        # model to invent one — that produced duplicate content in an
        # earlier version. Failing closed is the correct behavior.
        topic_str = (
            f"Topic: {topic}"
            if topic and str(topic).strip()
            else "Topic: (none supplied by the caller)"
        )

        knowledge_str = (
            f"\nUse the following knowledge base facts if relevant:\n{knowledge_context}\n"
            if knowledge_context
            else ""
        )

        style_guide, style_source = self._registry.resolve_for_brand(brand)

        # Brand charter. Sits immediately after the topic and before the
        # style guide so it has primary weight. The style guide is a
        # general template; the charter is this brand's specific voice.
        charter_block = self._brand_charter_block(brand)

        # Structured path gets the full schema block (which carries the
        # plain-text, voice, substance, and completeness rules). Prose
        # path gets the equivalent plain-text block.
        if structured:
            format_instruction = f"\n{POST_SCHEMA_PROMPT_BLOCK}\n"
        else:
            format_instruction = f"\n{_PROSE_PLAIN_TEXT_BLOCK}\n"

        task = f"""
Task:
Write a highly engaging social media post for {brand.name}.
{topic_str}
{charter_block}
{style_guide}

Guidelines:
1. Write in the exact tone and style of the persona defined above.
2. Use short paragraphs. If a list helps, use the "•" character. Do
   NOT use Markdown syntax.
3. Use emoji sparingly — as punctuation, not decoration.
4. Include a clear call to action (CTA) matching the '{ai_profile.cta_style}' style.
5. End with appropriate hashtags.
{format_instruction}
{brand_context}
{knowledge_str}
"""
        logger.debug(
            "Built post prompt: brand=%s style=%s registry_version=%s "
            "structured=%s charter_present=%s",
            brand.name, style_source, self._registry.version,
            structured, bool(charter_block),
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

        # Same charter the post path uses. Chat surfaces should honor the
        # declared brand voice too — otherwise the DM reply is generic
        # while the posts are specific.
        charter_block = self._brand_charter_block(brand)

        chat_context = f"""
Task Context:
You are conversing directly with {user_name} in a chat interface.
Brand Language: {brand.language}
{charter_block}
Guidelines:
1. Respond naturally, concisely, and consistently with your persona.
2. Keep responses brief since this is a chat interface.
3. Address the user's questions or comments thoughtfully.
4. Do NOT sound like a generic AI assistant. Maintain the {ai_profile.name} identity.
5. Do NOT use Markdown formatting. This is a plain-text chat surface.
6. Do NOT open with "As a [role], I've seen..." or "In today's...".
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
