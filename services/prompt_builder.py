"""
Prompt Builder Service.
Assembles complete LLM prompts using Brand and AIProfile contexts.

V2.3 (2026-10-27) — HASHTAG POLICY + TITLE-OPTIONAL:

  Two changes to how the pipeline produces content shape:

  1. HASHTAGS ARE NO LONGER REQUESTED FROM THE MODEL.
     The model used to be asked for 3-8 hashtags. On Facebook, Twitter/X,
     WhatsApp, hashtags do not drive discovery — the platforms deprecated
     hashtag-based discovery years ago. Instagram is the exception, but
     the pipeline doesn't target it. Producing hashtags from the model
     meant brand hashtags were either overridden or dropped (never
     merged consistently), and the model's topic tags were often generic
     (#technology, #innovation) or spammy (#tech, #life, #blessed).
     Now: the publishing layer (CaptionBuilder) appends the brand's
     configured hashtags. The prompt explicitly forbids a "hashtags"
     field. This eliminates wasted tokens, generic tags, and the merge
     problem entirely.

  2. TITLE IS OPTIONAL (was required).
     The TechPost schema previously required a 4-15 word title on every
     post. That title-only shape is a LinkedIn/blog convention. Facebook,
     Twitter/X, Instagram, and WhatsApp captions have no title — the
     first sentence IS the hook. Requiring a title forced the model to
     produce LinkedIn-essay structure regardless of the target platform,
     which is the exact failure the CONTENT_QUALITY_CONTRACT was written
     to prevent. Now: title is optional, and the platform contract
     decides whether to include one. Facebook/Twitter/Instagram/WhatsApp
     → no title. LinkedIn/blog → title OK if the model wants to add one.

V2.2 (2026-10-24) — MERGED CONTRACT + CHARTER + TRUNCATION-AWARE LENGTH:
  (unchanged from V2.2 — see full history in previous version)

V2.1 (2026-10-02) — BRAND CHARTER WIRING (preserved).
V2.0 (2026-10-02) — VOICE, SUBSTANCE, COMPLETENESS CONTRACT (preserved).
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
# V2.3 — Only "caption" is required. Title is optional (platform-dependent).
# Hashtags are NOT requested from the model. See module docstring.
#
POST_SCHEMA_REQUIRED_KEYS: tuple[str, ...] = ("caption",)

POST_SCHEMA_PROMPT_BLOCK = """
OUTPUT FORMAT — CRITICAL, DO NOT DEVIATE:
Respond with a SINGLE JSON object. No prose before or after. No markdown
fences. Do not explain your reasoning. Return raw JSON and nothing else.

Required keys:
{
  "caption": "the full body of the post, 60-120 words, plain text"
}

Optional keys (omit if the target platform does not use them):
{
  "title":  "4-15 word headline — ONLY for LinkedIn or blog targets. Omit for Facebook, Twitter/X, Instagram, WhatsApp.",
  "theme":  "one of: comparison, tutorial, checklist, architecture, code_tips, ai, security, database, backend, frontend, system_design, cloud"
}

DO NOT include a "hashtags" key. The publishing layer appends the brand's
standing hashtags automatically. Do not produce them yourself.

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
- For emphasis, use sentence structure — not asterisks.

LENGTH BUDGET — this is the single most important rule:
- Aim for 60-120 words. Do not exceed 150.
- The caption MUST end at a sentence boundary (period, question mark,
  or exclamation point). Do not end mid-sentence, mid-word, or with a
  trailing ellipsis.
- A shorter complete caption is always better than a longer truncated
  one. If you are unsure whether you have more to say, stop.

VOICE — do NOT write like a LinkedIn influencer:
- Do NOT open with "As a [role], I've seen...", "In today's...",
  "We've all been there", "As a seasoned...", or similar throat-clearing.
- Do NOT use "fear not", "friends", "let's dive in", "buckle up",
  "let's explore", "let's take a look".
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
- Close with a consequence or a specific next step. Not a rhetorical
  question.

Hard rules:
- "caption" MUST be present and non-empty.
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

LENGTH BUDGET:
- Aim for 60-120 words. Do not exceed 150.
- The caption MUST end at a sentence boundary (period, question mark,
  or exclamation point). Do not end mid-sentence, mid-word, or with
  a trailing ellipsis.
- A shorter complete caption is always better than a longer truncated
  one. If you are unsure whether you have more to say, stop.

VOICE — do NOT write like a LinkedIn influencer:
- Do NOT open with "As a [role], I've seen...", "In today's...",
  "We've all been there", "As a seasoned...", or similar throat-clearing.
- Do NOT use "fear not", "friends", "let's dive in", "buckle up",
  "let's explore", "let's take a look".
- Do NOT describe the topic. Make the point instead.
- Do NOT hedge when you could state the point directly.
- Do NOT close with "The key is..." or a summary section.
- Assume the reader is a developer with 3+ years of experience.

SUBSTANCE:
- Open with a specific, non-obvious claim. Not a restatement.
- Take a position where the topic allows.
- Include at least one concrete detail — a number, a scenario, a
  specific tool or version.
- Close with a consequence or a specific next step.
""".strip()


# ═══════════════════════════════════════════════════════════════════════════
# Content quality contract — universal rules for every brand and platform
# ═══════════════════════════════════════════════════════════════════════════
#
# These rules are structural, not topical. They apply to a devotional post
# on WhatsApp and a technical post on LinkedIn equally. The rules never
# tell the model what to say — they tell it what shape the output must
# take, and they forbid the specific patterns that mark AI-generated text.
#
CONTENT_QUALITY_CONTRACT = """
CONTENT QUALITY CONTRACT — every rule below is a hard requirement.

1. NAMED READER.
Write to one specific person, not to a category. Before you write, decide
who they are: their role, their situation, what they already know, what
they are worried about. The brand context describes the audience group;
narrow it to one person inside that group. Every sentence must be useful
to that specific person.

2. SPECIFIC FIRST SENTENCE.
The first sentence must contain something concrete: a claim, a number,
an event, a scene, a question the reader already has. Not a description
of the topic, not a restatement of the title, not a warm-up. The reader
decides in one sentence whether to continue.

3. ONE POINT, DEFENDED.
Do not survey. Pick one idea, one story, one lesson, one claim and
develop it. If you must mention alternatives, mention them as rejected
options ("I tried X first; here is what changed my mind"), not as a
balanced list.

4. EVIDENCE APPROPRIATE TO THE BRAND.
Ground the post in something the reader can verify or relate to. What
counts as evidence depends on the brand — a number, a config value, a
named tool, a scripture reference, a personal experience, a quote, a
customer story, a specific situation. The brand voice guide above states
which of these applies. The rule is: the reader should be able to say
"yes, I have seen that" or "no, but I understand the comparison". Do
not post in the abstract.

5. NO ENCYCLOPEDIC STRUCTURE.
Forbidden:
  - "Approach 1 / Approach 2 / Approach 3" or "Point 1 / Point 2 / Point 3"
  - "The Challenge / The Solution / The Result" as three equal sections
  - Pros/cons lists where every item gets equal weight
  - Any structure that could be replaced by a Wikipedia summary
Numbered lists only when the numbers carry information (steps you took,
things you observed, values you measured), not when they categorize
abstract ideas.

6. FORBIDDEN OPENERS.
Do not open with:
  - "In this post" / "In this article" / "Today we'll"
  - "As [group], we..." — describing the reader back to themselves
  - "As a seasoned [role]..." or any variant
  - "In today's fast-paced world" or any variant
  - "Let's dive in" / "Let's explore" / "Let's take a look"
  - "It's no secret that..." / "We all know that..."
  - A dictionary-style definition of the topic
  - A restatement of the title
Start with something only this post could say.

7. PEER VOICE.
Write as one person to another. Contractions are fine. First-person
singular is fine and often better than "we" — "we" is meaningless when
the reader does not know who else is in the room. Sentences may vary in
length; fragments are allowed. Avoid corporate verbs ("leverage",
"optimize", "deploy", "enable", "scale") unless the topic is literally
about those operations. The brand voice guide above tells you what
"peer" means for this audience — a technical peer, a fellow believer,
a fellow operator, a neighbour.

8. CLOSE WITH A SPECIFIC ENDING.
End with something concrete: the consequence of the point, the next
action, the specific question the post set up. Forbidden closes:
  - "What do you think?" (too generic)
  - "Let me know your thoughts!" (says nothing)
  - "Follow for more content like this."
  - Any sentence that sounds like a summary of the post.

9. EMOJI, IF USED, MUST BE SPECIFIC.
An emoji may appear only when it carries meaning — it points at the
subject, the emotion, or the moment. A decorative emoji picked because
it looked nice signals that the model is not thinking about the content.
When in doubt, use no emoji.

10. NO MARKDOWN UNLESS THE PLATFORM PERMITS IT.
Never emit markdown syntax in the caption: no **bold**, no *italic*,
no _underscore emphasis_, no # headers, no - bullets, no numbered lists
in markdown form, no `code` backticks. LinkedIn, Facebook, Twitter/X,
Instagram, and WhatsApp captions are plain text — the platforms render
these characters literally and the post looks unprofessional. Use blank
lines between paragraphs and full capitalization for emphasis.
""".strip()


# ═══════════════════════════════════════════════════════════════════════════
# Platform contracts — length, structure, hashtags, opening, emoji, formatting
# ═══════════════════════════════════════════════════════════════════════════
#
# V2.3 — Hashtag guidance relaxed. Only Instagram and LinkedIn mention
# hashtag counts at all (Instagram is the one platform where they still
# matter; LinkedIn mentions them as branding). Facebook/Twitter/WhatsApp
# contracts no longer instruct the model to produce hashtags — the
# publishing layer handles them.
#
_LINKEDIN_CONTRACT = """
PLATFORM CONTRACT — LinkedIn.
- Length: 60-120 words. Do not exceed 150.
- Structure: short paragraphs of 1-3 sentences, blank line between each.
  No markdown headers, no bold section titles.
- Formatting: plain text only. No markdown. No **bold**, no *italic*,
  no # headers, no - bullets. LinkedIn renders these characters literally.
- Opening: the first two lines appear in the preview. They must carry
  the hook. Do not use the first line as a warm-up.
- Closing: the consequence of the point, or a specific next step.
- Hashtags: the publishing layer appends 3-5 brand hashtags
  automatically. Do not produce hashtags yourself.
- Emoji: at most one per 100 words, only when meaningful.
""".strip()

_TWITTER_CONTRACT = """
PLATFORM CONTRACT — Twitter / X.
- Length: 240 characters max per tweet. If the idea needs more, write a
  thread of up to 15 tweets.
- Structure: one idea per tweet. Do not use "1/14" notation; the platform
  handles threading.
- Formatting: plain text only. No markdown. No **bold**, no *italic*,
  no # headers, no - bullets. Twitter/X renders these characters literally.
- Opening: the first tweet must stand alone as a claim or a question.
- Closing: the last tweet lands the consequence or next step.
- Hashtags: the publishing layer appends 1-2 brand hashtags automatically.
  Do not produce hashtags yourself.
- Emoji: 0-1 per tweet, only when meaningful.
""".strip()

_FACEBOOK_CONTRACT = """
PLATFORM CONTRACT — Facebook.
- Length: 60-120 words. Do not exceed 150.
- Structure: conversational, no headers. Short paragraphs.
- Formatting: plain text only. No markdown. No **bold**, no *italic*,
  no # headers, no - bullets. Facebook renders these characters literally.
  Use line breaks for pacing.
- Opening: the first line is the hook. Facebook truncates long posts
  behind "See more" at around 250 characters; make the first line count.
- Closing: the consequence of the point, or a specific next step.
- Hashtags: the publishing layer appends 1-3 brand hashtags
  automatically. Do not produce hashtags yourself.
- Emoji: occasional, meaningful.
""".strip()

_INSTAGRAM_CONTRACT = """
PLATFORM CONTRACT — Instagram.
- Length: 80-150 words for the caption. The first 125 characters are
  visible before "more" — they are the whole preview.
- Structure: line breaks between thoughts, no headers. The image carries
  the visual weight; the caption carries the story.
- Formatting: plain text only. No markdown. Instagram renders these
  characters literally.
- Opening: the first line is the entire visible preview. Make it earn
  the tap.
- Closing: the consequence or a specific next step.
- Hashtags: the publishing layer appends brand + topic hashtags
  automatically, up to 30 for this platform. Do not produce hashtags
  yourself.
- Emoji: allowed and often expected; each should be meaningful.
""".strip()

_WHATSAPP_CONTRACT = """
PLATFORM CONTRACT — WhatsApp (business broadcast / channel).
- Length: 40-100 words. Short-form.
- Structure: plain text, no markdown. Use line breaks for pacing only.
- Formatting: WhatsApp does not render markdown. Prefer plain text.
- Opening: the first line is the whole message preview.
- Closing: a single clear action or a specific consequence.
- Hashtags: do not produce hashtags. WhatsApp has no hashtag culture.
- Emoji: appropriate when the brand welcomes them. Not decoration.
""".strip()

_BLOG_CONTRACT = """
PLATFORM CONTRACT — Blog / long-form.
- Length: 600-1500 words depending on the topic.
- Structure: markdown headers allowed, sections flow into each other,
  one argument per section. Headers must carry information, not just
  label a section ("The 340ms threshold", not "Section 2").
- Formatting: markdown is permitted and expected.
- Opening: a hook in the first paragraph. Do not summarize the post or
  tell the reader what they will read.
- Closing: next steps, a specific question, or a clear offer.
- Hashtags: irrelevant for the body; use tags or keywords via metadata.
- Emoji: usually none.
""".strip()

PLATFORM_CONTRACTS: Dict[str, str] = {
    "linkedin": _LINKEDIN_CONTRACT,
    "twitter": _TWITTER_CONTRACT,
    "x": _TWITTER_CONTRACT,
    "facebook": _FACEBOOK_CONTRACT,
    "instagram": _INSTAGRAM_CONTRACT,
    "whatsapp": _WHATSAPP_CONTRACT,
    "blog": _BLOG_CONTRACT,
}

_DEFAULT_PLATFORM_CONTRACT = _FACEBOOK_CONTRACT


def resolve_platform_contract(platform: Optional[str]) -> str:
    """Return the platform contract that matches the target channel.

    V2.3 — Default is now Facebook (was LinkedIn). The primary use case
    for the platform is social content, and Facebook's constraints are
    the safest default for an unspecified social channel. Brands that
    target LinkedIn set brand.platform='linkedin' and get the LinkedIn
    contract explicitly.
    """
    key = (platform or "").strip().lower()
    return PLATFORM_CONTRACTS.get(key, _DEFAULT_PLATFORM_CONTRACT)


# ═══════════════════════════════════════════════════════════════════════════
# Style Registry — loads once at import, hot-swappable via reload()
# ═══════════════════════════════════════════════════════════════════════════

_STYLE_REGISTRY_PATH = Path(__file__).parent.parent / "config" / "content_styles.yaml"

# V2.3 — Fallback guide rewritten from a list of don'ts to a description
# of what to produce. Models follow positive instructions much more
# reliably than negative ones; the previous version listed only what to
# avoid, so the model fell back to its default voice (LinkedIn-inflected).
_SAFE_FALLBACK_GUIDE = """
Content Style: CONVERSATIONAL

Write like you're telling a friend something that just happened.

Shape:
- Open with the specific moment, fact, or observation. Not a definition.
  Not a general truth. Not a warm-up.
- One idea, developed. Not a survey. Not two sides of a debate.
- End with the consequence or the next step. Not a summary.

Voice:
- "I" and "you", not "we" and "one".
- Contractions are fine.
- Short sentences are fine. Fragments are fine.
- No corporate verbs (leverage, optimize, deploy, scale) unless the
  topic is literally about those operations.

Evidence for this brand: whatever the reader could point at and say
"yes, that." A number, a scene, a name, a quotation, a specific
situation. Do not invent facts. If you don't have evidence, write about
something else.

Peer for this brand: someone the writer knows by name. Not "our
audience". Not "developers". Not "believers". A person.

Emoji: only when a specific moment calls for one. Not as decoration.
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
                "platform_contracts": sorted(PLATFORM_CONTRACTS.keys()),
                "required_schema_keys": list(POST_SCHEMA_REQUIRED_KEYS),
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
        platform: Optional[str] = None,
    ) -> str:
        """Build a post-generation prompt.

        `structured=True` appends POST_SCHEMA_PROMPT_BLOCK so the model
        emits a JSON object matching POST_SCHEMA_REQUIRED_KEYS. Callers
        using the gateway's structured path MUST set this. Callers
        generating free prose (chat, fallback) MUST NOT — the prose
        path uses _PROSE_PLAIN_TEXT_BLOCK instead.

        `platform` selects the platform contract. Resolution order:
          1. Explicit argument.
          2. brand.platform attribute.
          3. Facebook default (V2.3).
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
            brand_context += f"Default Hashtags (appended by publishing layer): {brand.formatted_hashtags}\n"
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
        # style guide so it has primary weight.
        charter_block = self._brand_charter_block(brand)

        # Platform contract — the model writes for the target channel.
        resolved_platform = platform or getattr(brand, "platform", None)
        platform_contract = resolve_platform_contract(resolved_platform)

        # Format instruction: structured gets the full JSON schema block,
        # prose gets the plain-text block.
        if structured:
            format_instruction = f"\n{POST_SCHEMA_PROMPT_BLOCK}\n"
        else:
            format_instruction = f"\n{_PROSE_PLAIN_TEXT_BLOCK}\n"

        task = f"""
Task:
Write one social media post for {brand.name}.
{topic_str}
{charter_block}
--- BRAND VOICE GUIDE ---
{style_guide}

--- UNIVERSAL CONTENT QUALITY CONTRACT ---
{CONTENT_QUALITY_CONTRACT}

--- {platform_contract.splitlines()[0].lstrip('- ')} ---
{platform_contract}
{format_instruction}
--- BRAND CONTEXT ---
{brand_context}
{knowledge_str}
Before you write, decide these three things in silence:
  - Who is the specific reader inside the audience group?
  - What is the one point this post makes?
  - What evidence grounds it — the kind appropriate to this brand?

Then write the post. Do not include the three decisions in the output.
""".strip()

        logger.debug(
            "Built post prompt: brand=%s style=%s platform=%s registry_version=%s "
            "structured=%s charter_present=%s",
            brand.name, style_source, resolved_platform or "facebook",
            self._registry.version, structured, bool(charter_block),
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
    "resolve_platform_contract",
    "POST_SCHEMA_REQUIRED_KEYS",
    "POST_SCHEMA_PROMPT_BLOCK",
    "CONTENT_QUALITY_CONTRACT",
    "PLATFORM_CONTRACTS",
]
