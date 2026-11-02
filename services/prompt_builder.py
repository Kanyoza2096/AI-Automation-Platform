"""
Prompt Builder Service.
Assembles complete LLM prompts using Brand and AIProfile contexts.

V3.1 — LENGTH FLOOR 150 WORDS, 3-5 PARAGRAPHS, LAYERED STRUCTURE:

  Restores the substance of the months-ago captions (~180-200 words,
  4 paragraphs, each adding new information) that the current prompt
  was suppressing.

  1. LENGTH BUDGET raised from "60-120, floor 60" to "180-200, floor 150".
     The old 60-word floor was the operative constraint — the model
     landed at the minimum every time, producing 55-80 word captions
     that met the letter of the prompt and none of the intent.

  2. STRUCTURE block added. Each paragraph must add new information;
     restatement is forbidden. The months-ago captions used paragraph
     layers (claim → context → example → implication → question) and
     the current captions flattened into a single restating paragraph.

  3. Applies to both _PROSE_PLAIN_TEXT_BLOCK and POST_SCHEMA_PROMPT_BLOCK
     so both generation paths get the same length and structure rules.

V3.0 — DIRECTION REMOVED + GROUNDING STRENGTHENED (preserved):
  - _brand_charter_block returns "" (direction feature deprecated).
  - VOICE / GROUNDING / FORBIDDEN STRUCTURE rules prohibit fabricated
    metrics, first-person case studies, and before/after/next-step
    templates.

V2.6 — KB-DRIVEN TOPIC AXIS ROTATION (preserved).
V2.3 — HASHTAG POLICY + TITLE-OPTIONAL (preserved).
V2.2 — MERGED CONTRACT + CHARTER + TRUNCATION-AWARE LENGTH (preserved).
V2.1 — BRAND CHARTER WIRING (deprecated in V3.0).
V2.0 — VOICE, SUBSTANCE, COMPLETENESS CONTRACT (preserved).
V1.9 — PLAIN-TEXT OUTPUT (preserved).
V1.8 — STRUCTURED-OUTPUT SUPPORT (preserved).
V1.7 — PERMANENT DESIGN (preserved).
"""

from __future__ import annotations

import logging
import random
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
# Only "caption" is required. Title is optional (platform-dependent).
# Hashtags are NOT requested from the model. See module docstring.
#
POST_SCHEMA_REQUIRED_KEYS: tuple[str, ...] = ("caption",)

POST_SCHEMA_PROMPT_BLOCK = """
OUTPUT FORMAT — CRITICAL, DO NOT DEVIATE:
Respond with a SINGLE JSON object. No prose before or after. No markdown
fences. Do not explain your reasoning. Return raw JSON and nothing else.

Required keys:
{
  "caption": "the full body of the post, 180-200 words, plain text, 3-5 paragraphs"
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
- Aim for 180-200 words. Do not exceed 220.
- Do not go below 150 words. A caption shorter than 150 words is
  almost certainly underdeveloped — it means you have not yet found
  the specific claim and developed it. Find the claim, develop it,
  then write. The word count follows from the specificity, not the
  other way around.
- The caption MUST end at a sentence boundary (period, question mark,
  or exclamation point). Do not end mid-sentence, mid-word, or with
  a trailing ellipsis.

STRUCTURE — the post must build, not restate:
- Paragraph 1: the claim or observation. Something specific.
- Paragraph 2: why it matters, or what is usually misunderstood.
- Paragraph 3: an illustrative example, a specific tool, or a
  scenario that makes the point concrete.
- Paragraph 4 (optional): the implication — what the reader does
  differently.
- Closing (optional): a specific question, not a generic invitation
  to comment.
- Target 3-5 paragraphs. Each paragraph must add new information.
  Do not restate the previous paragraph's point in different words.
- If a sentence could be deleted without the reader losing anything,
  delete it. Every sentence must earn its place.
- Separate paragraphs with a single blank line.

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

VOICE — first person is FORBIDDEN unless the retrieved passages use it:
- Do NOT write "our system," "our team," "our production," "we
  reduced," "we implemented," "we learned."
- Most handbook and reference content is third person. Match it.
- Wrong: "We cut agent restarts by 30% by implementing idempotent
  task handling."
- Right: "Idempotent task handling lets a long-running agent
  checkpoint and replay without corrupting state."
- The reader can tell the difference between a real case study and
  a fabricated one. Fabricated first-person destroys credibility.

GROUNDING — where does the content come from:
- Every claim in the post must be traceable to one of the retrieved
  passages. If the passage says "durable state survives restarts,"
  you may say that. You may NOT say "we reduced latency by 30%."
- A conceptual claim from the passage IS the evidence. You do not
  need to add a metric. A specific idea, correctly expressed, is
  more credible than an invented case study.
- If the retrieved passages contain a specific metric, tool, or
  scenario, you may use it. If they do not, do NOT invent one.
- Close with the practical consequence of the concept. What does
  the reader do differently? Not a fabricated next step.

FORBIDDEN STRUCTURE — do NOT write a case study:
- Do NOT open with "We [verb]ed [thing] and [outcome]."
- Do NOT use a "before state → after state → next step" template.
- Do NOT include invented metrics, percentages, or latency numbers
  unless they appear in a retrieved passage.
- Do NOT close with "Next step is to..." or "We plan to..."
- The post is a reflection, not a postmortem summary.

Hard rules:
- "caption" MUST be present and non-empty.
- Return raw JSON, not a Markdown code block.
- Do not add commentary outside the JSON object.
""".strip()


# Prose path (structured=False) needs the same plain-text, voice,
# grounding, length, and structure rules. Without them, the model
# defaults to Markdown, the LinkedIn-influencer register, single-paragraph
# restatement, and 60-word status updates.
_PROSE_PLAIN_TEXT_BLOCK = """
PLAIN-TEXT RULES — the post is published to a plain-text platform:
- Do NOT use Markdown syntax. No **, __, *, _, ##, `, or >.
- For lists, use the bullet character "•" followed by a space.
- For line breaks, use actual line breaks (a single blank line between
  paragraphs).
- For emphasis, use sentence structure — not asterisks.

LENGTH BUDGET:
- Aim for 180-200 words. Do not exceed 220.
- Do not go below 150 words. A caption shorter than 150 words is
  almost certainly underdeveloped — it means you have not yet found
  the specific claim and developed it. Find the claim, develop it,
  then write. The word count follows from the specificity, not the
  other way around.
- The caption MUST end at a sentence boundary (period, question mark,
  or exclamation point). Do not end mid-sentence, mid-word, or with
  a trailing ellipsis.

STRUCTURE — the post must build, not restate:
- Paragraph 1: the claim or observation. Something specific.
- Paragraph 2: why it matters, or what is usually misunderstood.
- Paragraph 3: an illustrative example, a specific tool, or a
  scenario that makes the point concrete.
- Paragraph 4 (optional): the implication — what the reader does
  differently.
- Closing (optional): a specific question, not a generic invitation
  to comment.
- Target 3-5 paragraphs. Each paragraph must add new information.
  Do not restate the previous paragraph's point in different words.
- If a sentence could be deleted without the reader losing anything,
  delete it. Every sentence must earn its place.
- Separate paragraphs with a single blank line.

VOICE — do NOT write like a LinkedIn influencer:
- Do NOT open with "As a [role], I've seen...", "In today's...",
  "We've all been there", "As a seasoned...", or similar throat-clearing.
- Do NOT use "fear not", "friends", "let's dive in", "buckle up",
  "let's explore", "let's take a look".
- Do NOT describe the topic. Make the point instead.
- Do NOT hedge when you could state the point directly.
- Do NOT close with "The key is..." or a summary section.
- Assume the reader is a developer with 3+ years of experience.

VOICE — first person is FORBIDDEN unless the retrieved passages use it:
- Do NOT write "our system," "our team," "our production," "we
  reduced," "we implemented," "we learned."
- Most handbook and reference content is third person. Match it.
- Wrong: "We cut agent restarts by 30% by implementing idempotent
  task handling."
- Right: "Idempotent task handling lets a long-running agent
  checkpoint and replay without corrupting state."
- The reader can tell the difference between a real case study and
  a fabricated one. Fabricated first-person destroys credibility.

GROUNDING — where does the content come from:
- Every claim in the post must be traceable to one of the retrieved
  passages. If the passage says "durable state survives restarts,"
  you may say that. You may NOT say "we reduced latency by 30%."
- A conceptual claim from the passage IS the evidence. You do not
  need to add a metric. A specific idea, correctly expressed, is
  more credible than an invented case study.
- If the retrieved passages contain a specific metric, tool, or
  scenario, you may use it. If they do not, do NOT invent one.
- Close with the practical consequence of the concept. What does
  the reader do differently? Not a fabricated next step.

FORBIDDEN STRUCTURE — do NOT write a case study:
- Do NOT open with "We [verb]ed [thing] and [outcome]."
- Do NOT use a "before state → after state → next step" template.
- Do NOT include invented metrics, percentages, or latency numbers
  unless they appear in a retrieved passage.
- Do NOT close with "Next step is to..." or "We plan to..."
- The post is a reflection, not a postmortem summary.
""".strip()


# ═══════════════════════════════════════════════════════════════════════════
# Content quality contract — universal rules for every brand and platform
# ═══════════════════════════════════════════════════════════════════════════
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
customer story, a specific situation, OR a conceptual claim from the
retrieved passages.

For technical brands whose corpus is reference text (handbooks,
textbooks, algorithm references), the CONCEPT ITSELF is the evidence.
"Union-find with path compression gives amortized near-constant time"
is sufficient. Do not invent a metric to satisfy a "be concrete"
instinct — fabricated metrics destroy credibility faster than any
conceptual abstraction.

The rule is: the reader should be able to say "yes, I have seen that"
or "no, but I understand the comparison." Do not post in the abstract.
Do not fabricate. If the retrieved passages contain no metric, state
the concept — don't invent one.

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
  - "We [verb]ed X and [outcome]" — this is a fabricated case study opener
Start with something only this post could say.

7. PEER VOICE.
Write as one person to another. Contractions are fine. First-person
singular is fine and often better than "we" — "we" is meaningless when
the reader does not know who else is in the room. First-person plural
("we," "our") is FORBIDDEN unless the retrieved passages use it —
fabricated first-person reads as a case study, which the reader can
detect and rejects. Sentences may vary in length; fragments are allowed.
Avoid corporate verbs ("leverage", "optimize", "deploy", "enable",
"scale") unless the topic is literally about those operations.

8. CLOSE WITH A SPECIFIC ENDING.
End with something concrete: the consequence of the point, the next
action, the specific question the post set up. Forbidden closes:
  - "What do you think?" (too generic)
  - "Let me know your thoughts!" (says nothing)
  - "Follow for more content like this."
  - "Next step is to..." (fabricated case-study close)
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
# Platform contracts
# ═══════════════════════════════════════════════════════════════════════════
_LINKEDIN_CONTRACT = """
PLATFORM CONTRACT — LinkedIn.
- Length: 180-200 words. Do not exceed 220.
- Structure: short paragraphs of 2-4 sentences, blank line between each.
  No markdown headers, no bold section titles.
- Formatting: plain text only. No markdown. No **bold**, no *italic*,
  no # headers, no - bullets. LinkedIn renders these characters literally.
- Opening: the first two lines appear in the preview. They must carry
  the hook. Do not use the first line as a warm-up.
- Closing: the consequence of the point, or a specific question.
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
- Length: 180-200 words. Do not exceed 220.
- Structure: 3-5 paragraphs of 2-4 sentences each. Blank line between
  paragraphs. The first paragraph must carry the hook because Facebook
  truncates at ~250 characters behind "See more".
- Formatting: plain text only. No markdown. No **bold**, no *italic*,
  no # headers, no - bullets. Facebook renders these characters literally.
- Opening: the first line is the hook. Make it count.
- Closing: the consequence of the point, or a specific question.
- Hashtags: the publishing layer appends 1-3 brand hashtags
  automatically. Do not produce hashtags yourself.
- Emoji: occasional, meaningful.
""".strip()

_INSTAGRAM_CONTRACT = """
PLATFORM CONTRACT — Instagram.
- Length: 150-200 words for the caption. The first 125 characters are
  visible before "more" — they are the whole preview.
- Structure: line breaks between thoughts, 3-5 paragraphs. The image
  carries the visual weight; the caption carries the story.
- Formatting: plain text only. No markdown. Instagram renders these
  characters literally.
- Opening: the first line is the entire visible preview. Make it earn
  the tap.
- Closing: the consequence or a specific question.
- Hashtags: the publishing layer appends brand + topic hashtags
  automatically, up to 30 for this platform. Do not produce hashtags
  yourself.
- Emoji: allowed and often expected; each should be meaningful.
""".strip()

_WHATSAPP_CONTRACT = """
PLATFORM CONTRACT — WhatsApp (business broadcast / channel).
- Length: 60-120 words. Short-form.
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
    """Return the platform contract that matches the target channel."""
    key = (platform or "").strip().lower()
    return PLATFORM_CONTRACTS.get(key, _DEFAULT_PLATFORM_CONTRACT)


def _contract_heading(contract: str) -> str:
    """First line of a platform contract, without the leading '- '."""
    first_line = contract.splitlines()[0] if contract else ""
    return first_line.lstrip("- ").strip()


# ═══════════════════════════════════════════════════════════════════════════
# Style Registry
# ═══════════════════════════════════════════════════════════════════════════

_STYLE_REGISTRY_PATH = Path(__file__).parent.parent / "config" / "content_styles.yaml"

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
situation, or a conceptual claim from the retrieved passages. Do not
invent facts. If you don't have evidence, state the concept — don't
fabricate.

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

    def infer_style_for(self, brand: Brand) -> str:
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

    def resolve_for_brand(self, brand: Brand) -> tuple[str, str]:
        custom = getattr(brand, "custom_style_guide", None)
        if isinstance(custom, str) and custom.strip():
            return custom.strip(), "custom"

        key = self.infer_style_for(brand)
        guide = self.guide_for(key)
        if guide:
            return guide, f"registry:{key}"
        return _SAFE_FALLBACK_GUIDE.strip(), "fallback"

    def guide_for_brand(self, brand: Brand) -> str:
        guide, _ = self.resolve_for_brand(brand)
        return guide


_style_registry = StyleRegistry()


def get_style_registry() -> StyleRegistry:
    return _style_registry


def reload_style_registry() -> None:
    _style_registry.reload()


# ═══════════════════════════════════════════════════════════════════════════
# Knowledge-base theme lookup
# ═══════════════════════════════════════════════════════════════════════════

def _get_kb_themes(brand: Brand) -> Dict[str, str]:
    """Return {theme: description} from the brand's knowledge base."""
    try:
        from services.knowledge_base import get_brand_themes
        themes = get_brand_themes(
            brand_id=getattr(brand, "id", None),
            workspace_id=getattr(brand, "workspace_id", None),
        )
        return dict(themes) if themes else {}
    except Exception as exc:
        logger.warning("KB theme lookup failed, continuing without: %s", exc)
        return {}


# ═══════════════════════════════════════════════════════════════════════════
# PromptBuilder
# ═══════════════════════════════════════════════════════════════════════════

class PromptBuilder:
    """Service responsible for generating context-aware prompts."""

    service_name = "prompt_builder"

    def __init__(self, style_registry: Optional[StyleRegistry] = None):
        self._registry = style_registry or _style_registry
        self.last_used_axis: Optional[str] = None

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

    @staticmethod
    def _default_brand() -> Brand:
        from models.brand import Brand as _Brand
        return _Brand.create_default("default")

    @staticmethod
    def _default_ai_profile() -> AIProfile:
        from models.ai_profile import AIProfile as _AIProfile
        return _AIProfile.create_default("default")

    @staticmethod
    def _brand_charter_block(brand: Brand) -> str:
        """DEPRECATED in V3.0 — always returns "".

        Direction-based topic generation was removed in TopicSelector V3.0.
        The charter was the model-side input to that feature. Keeping it
        out of the prompt removes the last path by which
        brand.topic_direction could override the grounding rules.
        """
        return ""

    @staticmethod
    def _brand_context_block(brand: Brand) -> str:
        lines = [
            f"Brand Tone: {brand.tone}",
            f"Brand Language: {brand.language}",
        ]
        if getattr(brand, "hashtags", None):
            lines.append(
                "Default Hashtags (appended by publishing layer): "
                f"{brand.formatted_hashtags}"
            )
        style_category = getattr(brand, "style_category", None)
        if style_category:
            lines.append(f"Style Category: {style_category}")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _select_topic_axis(
        brand: Brand,
        last_used_axis: Optional[str],
    ) -> tuple[Optional[str], Optional[str]]:
        """Select a topic axis for this post."""
        kb_themes = _get_kb_themes(brand)
        brand_axes: Mapping[str, Any] = getattr(brand, "topic_axes", None) or {}

        def pick(pool: List[str]) -> str:
            fresh = [k for k in pool if k != last_used_axis]
            return random.choice(fresh or pool)

        if brand_axes and kb_themes:
            overlapping = [k for k in brand_axes if k in kb_themes]
            if overlapping:
                selected = pick(overlapping)
                description = brand_axes.get(selected) or kb_themes.get(selected) or ""
                return selected, str(description)

        if kb_themes:
            selected = pick(list(kb_themes.keys()))
            return selected, str(kb_themes.get(selected) or "")

        if brand_axes:
            selected = pick(list(brand_axes.keys()))
            return selected, str(brand_axes.get(selected) or "")

        return None, None

    @staticmethod
    def _axis_block(axis_name: Optional[str], axis_description: Optional[str]) -> str:
        if not axis_name:
            return ""
        desc_line = f"{axis_description}\n" if axis_description else ""
        return (
            f'\nTODAY\'S TOPIC AXIS: "{axis_name}"\n'
            f"{desc_line}\n"
            f"The post MUST be about this specific axis. Do not blend other "
            f"axes into it. Do not write a generic statement about the brand. "
            f"Do not open with a warm-up. Focus entirely on '{axis_name}'.\n"
        )

    def build_post_prompt(
        self,
        brand: Optional[Brand] = None,
        ai_profile: Optional[AIProfile] = None,
        topic: Optional[str] = None,
        knowledge_context: Optional[str] = None,
        structured: bool = False,
        platform: Optional[str] = None,
        last_used_axis: Optional[str] = None,
    ) -> str:
        if brand is None:
            brand = self._default_brand()
        if ai_profile is None:
            ai_profile = self._default_ai_profile()

        self.last_used_axis = None

        system_prompt = ai_profile.build_system_prompt(
            brand_name=brand.name,
            audience=brand.audience_description,
        )

        brand_context = self._brand_context_block(brand)

        axis_name, axis_desc = self._select_topic_axis(brand, last_used_axis)
        if axis_name is not None:
            self.last_used_axis = axis_name
            logger.debug(
                "Topic axis selected: %s (last used: %s)",
                axis_name, last_used_axis,
            )
        axis_str = self._axis_block(axis_name, axis_desc)

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

        charter_block = self._brand_charter_block(brand)

        resolved_platform = platform or getattr(brand, "platform", None)
        platform_contract = resolve_platform_contract(resolved_platform)
        platform_heading = _contract_heading(platform_contract)

        format_block = POST_SCHEMA_PROMPT_BLOCK if structured else _PROSE_PLAIN_TEXT_BLOCK

        task = f"""
Task:
Write one social media post for {brand.name}.
{topic_str}
{axis_str}{charter_block}
--- BRAND VOICE GUIDE ---
{style_guide}

--- UNIVERSAL CONTENT QUALITY CONTRACT ---
{CONTENT_QUALITY_CONTRACT}

--- {platform_heading} ---
{platform_contract}

{format_block}

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
            "structured=%s axis=%s",
            brand.name, style_source, resolved_platform or "facebook",
            self._registry.version, structured, axis_name or "none",
        )
        return f"{system_prompt}\n\n{task}"

    def build_chat_prompt(
        self,
        brand: Optional[Brand] = None,
        ai_profile: Optional[AIProfile] = None,
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
