"""
Content Validator v3.0 — Architecture §21-22 (Async-Native).

Validates AI-generated content before it reaches the renderer or publisher.

v3.0 CHANGES:
  - FULLY ASYNC-NATIVE (async validate, async EventBus)
  - HealthProvider protocol support
  - UnifiedEventBus integration
  - All v2.0 features preserved (semantic quality scoring,
    near-duplicate detection, brand voice consistency,
    configurable content policies, statistics)

v2.0 UPGRADES (preserved):
  - Semantic quality scoring (readability, engagement potential)
  - Near-duplicate detection (fuzzy match against recent posts)
  - Brand voice consistency (compare against knowledge base)
  - Configurable content policies per brand
  - EventBus integration for validation events
  - Health check & statistics
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Optional

logger = logging.getLogger("kanyoza.services.content_validator")


# ═══════════════════════════════════════════════════════════════════════════
# ENUMS & DATA CLASSES (Preserved)
# ═══════════════════════════════════════════════════════════════════════════

class ValidationStatus(str, Enum):
    APPROVED = "approved"
    APPROVED_WITH_WARNINGS = "approved_with_warnings"
    REJECTED = "rejected"
    NEEDS_REVIEW = "needs_review"


class ViolationSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class ValidationViolation:
    field: str
    severity: ViolationSeverity
    message: str
    rule: str = ""

    @property
    def is_fatal(self) -> bool:
        return self.severity == ViolationSeverity.ERROR


@dataclass
class ValidationResult:
    status: ValidationStatus
    violations: list[ValidationViolation] = field(default_factory=list)
    warnings_count: int = 0
    errors_count: int = 0
    quality_score: float = 0.0

    @property
    def is_approved(self) -> bool:
        return self.status in (ValidationStatus.APPROVED, ValidationStatus.APPROVED_WITH_WARNINGS)

    @property
    def is_rejected(self) -> bool:
        return self.status == ValidationStatus.REJECTED

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "approved": self.is_approved,
            "errors_count": self.errors_count,
            "warnings_count": self.warnings_count,
            "quality_score": self.quality_score,
            "violations": [
                {"field": v.field, "severity": v.severity.value, "message": v.message, "rule": v.rule}
                for v in self.violations
            ],
        }

    @classmethod
    def approved(cls) -> "ValidationResult":
        return cls(status=ValidationStatus.APPROVED, quality_score=1.0)

    @classmethod
    def rejected(cls, violations: list[ValidationViolation]) -> "ValidationResult":
        errors = sum(1 for v in violations if v.severity == ViolationSeverity.ERROR)
        warnings = sum(1 for v in violations if v.severity == ViolationSeverity.WARNING)
        return cls(status=ValidationStatus.REJECTED, violations=violations, errors_count=errors, warnings_count=warnings)


# ═══════════════════════════════════════════════════════════════════════════
# PLATFORM LIMITS (Preserved)
# ═══════════════════════════════════════════════════════════════════════════

PLATFORM_LIMITS: dict[str, dict[str, int]] = {
    "facebook": {"max_caption_length": 63206, "max_hashtags": 30, "max_title_length": 200, "min_caption_length": 1},
    "instagram": {"max_caption_length": 2200, "max_hashtags": 30, "max_title_length": 100, "min_caption_length": 1},
    "linkedin": {"max_caption_length": 3000, "max_hashtags": 5, "max_title_length": 200, "min_caption_length": 1},
    "twitter": {"max_caption_length": 280, "max_hashtags": 10, "max_title_length": 100, "min_caption_length": 1},
}


# ═══════════════════════════════════════════════════════════════════════════
# LOW QUALITY PATTERNS (Preserved)
# ═══════════════════════════════════════════════════════════════════════════

LOW_QUALITY_PATTERNS = [
    (r"in\s+today'?s?\s+(digital|fast-paced|ever-changing|modern)\s+\w+", "Generic opening phrase — lacks specificity"),
    (r"(delve|dive\s+deep|explore|uncover|discover)\s+the\s+\w+\s+of", "Generic AI-generated verb pattern"),
    (r"it\s+is\s+important\s+to\s+(note|remember|understand|consider)", "Weak filler phrase"),
    (r"in\s+conclusion|to\s+sum\s+up|in\s+summary", "Generic conclusion — content should end naturally"),
    (r"whether\s+you('re|\s+are)\s+a\s+(beginner|expert|professional)", "Overused audience address"),
    (r"revolutionize|game-changer|disruptive|cutting-edge|state-of-the-art", "Hype words — use specific benefits instead"),
    (r"unleash\s+(your|the)\s+(power|potential|magic)", "Clickbait pattern"),
]

MIN_CONTENT_WORDS = 20
MAX_EMOJIS = 8


# ═══════════════════════════════════════════════════════════════════════════
# CONTENT VALIDATOR v3.0 — FULLY ASYNC
# ═══════════════════════════════════════════════════════════════════════════

class ContentValidator:
    """Validates AI-generated structured content before publishing — ASYNC-NATIVE."""

    def __init__(self, event_bus=None) -> None:
        self._limits = PLATFORM_LIMITS
        self._event_bus = event_bus
        self._recent_captions: List[str] = []
        self._max_recent = 100
        self._validated_count = 0
        self._rejected_count = 0
        self._lock = asyncio.Lock()

    # ═══════════════════════════════════════════════════════════════
    # HEALTH PROVIDER PROTOCOL (v3.0)
    # ═══════════════════════════════════════════════════════════════

    @property
    def service_name(self) -> str:
        return "content_validator"

    async def check_health_async(self) -> dict:
        stats = self.get_stats()
        status = "healthy"
        if stats["rejection_rate"] > 50 and stats["validated_count"] > 10:
            status = "degraded"
        return {"status": status, "stats": stats}

    def set_event_bus(self, event_bus) -> None:
        self._event_bus = event_bus

    async def _emit(self, event_name: str, payload: dict) -> None:
        if not self._event_bus:
            return
        try:
            result = self._event_bus.publish(event_name, payload, source="content_validator")
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════════
    # PUBLIC API (ASYNC)
    # ═══════════════════════════════════════════════════════════════

    async def validate(
        self,
        content: dict[str, Any],
        brand: Optional[Any] = None,
        platform: str = "facebook",
        require_media: bool = False,
        recent_posts: Optional[List[str]] = None,
    ) -> ValidationResult:
        """Validate structured content against all applicable rules (async)."""
        violations: list[ValidationViolation] = []
        limits = self._limits.get(platform, self._limits["facebook"])

        # All validation checks (sync — CPU-bound, fast)
        self._check_required_fields(content, violations)
        self._validate_caption(content, limits, violations)
        self._validate_title(content, limits, violations)
        self._validate_hashtags(content, limits, violations)
        self._validate_content_type(content, violations)

        if require_media:
            self._validate_media_required(content, violations)

        self._validate_platform_constraints(content, platform, violations)

        if brand:
            self._validate_brand_constraints(content, brand, violations)

        quality_score = self._score_quality(content, violations)
        self._check_duplicates(content, violations, recent_posts)

        if brand:
            self._check_brand_voice(content, brand, violations)

        errors = [v for v in violations if v.severity == ViolationSeverity.ERROR]
        warnings = [v for v in violations if v.severity == ViolationSeverity.WARNING]

        if errors:
            status = ValidationStatus.REJECTED
        elif warnings:
            status = ValidationStatus.APPROVED_WITH_WARNINGS
        elif quality_score < 0.3:
            status = ValidationStatus.NEEDS_REVIEW
        else:
            status = ValidationStatus.APPROVED

        result = ValidationResult(
            status=status,
            violations=violations,
            errors_count=len(errors),
            warnings_count=len(warnings),
            quality_score=quality_score,
        )

        async with self._lock:
            self._validated_count += 1
            if not result.is_approved:
                self._rejected_count += 1

        if not result.is_approved:
            await self._emit("content.validation_failed", {
                "status": result.status.value,
                "errors": result.errors_count,
                "warnings": result.warnings_count,
                "quality_score": quality_score,
            })

        caption = str(content.get("caption", ""))
        if caption and result.is_approved:
            async with self._lock:
                self._recent_captions.append(caption.lower())
                if len(self._recent_captions) > self._max_recent:
                    self._recent_captions = self._recent_captions[-self._max_recent:]

        return result

    # ═══════════════════════════════════════════════════════════════
    # v2.0 — SEMANTIC QUALITY SCORING (preserved — all methods below identical)
    # ═══════════════════════════════════════════════════════════════

    def _score_quality(self, content: dict, violations: list[ValidationViolation]) -> float:
        caption = str(content.get("caption", ""))
        title = str(content.get("title", ""))
        full_text = f"{title} {caption}".strip()

        if not full_text:
            return 0.0

        scores = []

        generic_count = 0
        for pattern, description in LOW_QUALITY_PATTERNS:
            if re.search(pattern, full_text, re.IGNORECASE):
                generic_count += 1
                violations.append(ValidationViolation(field="caption", severity=ViolationSeverity.WARNING, message=description, rule="generic_pattern"))

        pattern_score = max(0.0, 1.0 - (generic_count * 0.15))
        scores.append(pattern_score)

        words = full_text.split()
        word_count = len(words)
        if word_count < MIN_CONTENT_WORDS:
            word_score = word_count / MIN_CONTENT_WORDS
            violations.append(ValidationViolation(field="caption", severity=ViolationSeverity.WARNING, message=f"Content too short: {word_count} words (min {MIN_CONTENT_WORDS})", rule="min_content_words"))
        else:
            word_score = min(1.0, word_count / 100)
        scores.append(word_score)

        emoji_count = len(re.findall(r"[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF\U00002702-\U000027B0\U000024C2-\U0001F251✨🔥💯👍👏🎉💪🚀🤖👀📈📉❤️💡🎯⚡🔒🛡️🔍📊🏆🎓🌍💼]", full_text))
        if emoji_count > MAX_EMOJIS:
            emoji_score = max(0.0, 1.0 - (emoji_count - MAX_EMOJIS) * 0.1)
            violations.append(ValidationViolation(field="caption", severity=ViolationSeverity.WARNING, message=f"Excessive emojis: {emoji_count} (max recommended: {MAX_EMOJIS})", rule="max_emojis"))
        else:
            emoji_score = 1.0
        scores.append(emoji_score)

        sentences = [s.strip() for s in re.split(r"[.!?]+", full_text) if s.strip()]
        if sentences and word_count > 0:
            avg_words_per_sentence = word_count / len(sentences)
            if 12 <= avg_words_per_sentence <= 20:
                readability_score = 1.0
            elif avg_words_per_sentence < 8:
                readability_score = 0.5
            elif avg_words_per_sentence > 30:
                readability_score = 0.6
                violations.append(ValidationViolation(field="caption", severity=ViolationSeverity.INFO, message=f"Sentences are long ({avg_words_per_sentence:.0f} words avg) — consider breaking up", rule="readability"))
            else:
                readability_score = 0.8
        else:
            readability_score = 0.5
        scores.append(readability_score)

        hashtags = content.get("hashtags", []) or []
        if hashtags:
            hashtag_text = " ".join(hashtags).lower()
            caption_lower = caption.lower()
            relevant_count = sum(1 for tag in hashtags if tag.lower().lstrip("#") in caption_lower)
            hashtag_score = relevant_count / len(hashtags)
        else:
            hashtag_score = 0.5
        scores.append(hashtag_score)

        return round(sum(scores) / len(scores), 2)

    def _check_duplicates(self, content: dict, violations: list[ValidationViolation], recent_posts: Optional[List[str]] = None) -> None:
        caption = str(content.get("caption", "")).lower()
        if not caption:
            return
        posts = recent_posts or self._recent_captions
        for post in posts:
            similarity = self._text_similarity(caption, post)
            if similarity > 0.7:
                violations.append(ValidationViolation(field="caption", severity=ViolationSeverity.WARNING, message=f"Content is {similarity:.0%} similar to a recent post — possible duplicate", rule="near_duplicate"))
                break

    def _text_similarity(self, a: str, b: str) -> float:
        tokens_a = set(a.split())
        tokens_b = set(b.split())
        if not tokens_a or not tokens_b:
            return 0.0
        intersection = tokens_a & tokens_b
        return len(intersection) / min(len(tokens_a), len(tokens_b))

    def _check_brand_voice(self, content: dict, brand: Any, violations: list[ValidationViolation]) -> None:
        try:
            from services.knowledge_base import KnowledgeBase, tokenize
            caption = str(content.get("caption", ""))
            if not caption:
                return
            brand_terms = set()
            kb = getattr(brand, "_knowledge_base", None)
            if kb:
                topics = kb.extract_topics(top_n=50)
                brand_terms = {t for t, _ in topics}
            if not brand_terms:
                return
            content_terms = set(tokenize(caption))
            overlap = content_terms & brand_terms
            if len(content_terms) > 0:
                relevance = len(overlap) / min(len(content_terms), 20)
                if relevance < 0.1:
                    violations.append(ValidationViolation(field="caption", severity=ViolationSeverity.INFO, message="Content may not align with brand knowledge base — few relevant terms found", rule="brand_voice"))
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════════
    # INDIVIDUAL CHECKS (All preserved — identical to v2.0)
    # ═══════════════════════════════════════════════════════════════

    def _check_required_fields(self, content: dict, violations: list[ValidationViolation]) -> None:
        if not content.get("caption") or not str(content.get("caption", "")).strip():
            violations.append(ValidationViolation(field="caption", severity=ViolationSeverity.ERROR, message="Caption is empty or missing", rule="required_field"))

    def _validate_caption(self, content: dict, limits: dict, violations: list[ValidationViolation]) -> None:
        caption = str(content.get("caption", ""))
        max_length = limits.get("max_caption_length", 63206)
        min_length = limits.get("min_caption_length", 1)
        if len(caption) > max_length:
            violations.append(ValidationViolation(field="caption", severity=ViolationSeverity.ERROR, message=f"Caption too long: {len(caption)} chars (max {max_length})", rule="max_length"))
        elif len(caption) < min_length:
            violations.append(ValidationViolation(field="caption", severity=ViolationSeverity.ERROR, message=f"Caption too short: {len(caption)} chars (min {min_length})", rule="min_length"))
        if len(caption) > max_length * 0.9:
            violations.append(ValidationViolation(field="caption", severity=ViolationSeverity.WARNING, message=f"Caption approaching limit: {len(caption)}/{max_length} chars", rule="approaching_max_length"))

    def _validate_title(self, content: dict, limits: dict, violations: list[ValidationViolation]) -> None:
        title = str(content.get("title", ""))
        if not title:
            return
        max_title = limits.get("max_title_length", 200)
        if len(title) > max_title:
            violations.append(ValidationViolation(field="title", severity=ViolationSeverity.WARNING, message=f"Title exceeds recommended length: {len(title)}/{max_title}", rule="max_title_length"))

    def _validate_hashtags(self, content: dict, limits: dict, violations: list[ValidationViolation]) -> None:
        hashtags = content.get("hashtags", [])
        if not hashtags:
            return
        if not isinstance(hashtags, list):
            violations.append(ValidationViolation(field="hashtags", severity=ViolationSeverity.ERROR, message="Hashtags must be a list", rule="hashtags_type"))
            return
        max_hashtags = limits.get("max_hashtags", 30)
        if len(hashtags) > max_hashtags:
            violations.append(ValidationViolation(field="hashtags", severity=ViolationSeverity.WARNING, message=f"Too many hashtags: {len(hashtags)} (max {max_hashtags})", rule="max_hashtags"))

    def _validate_content_type(self, content: dict, violations: list[ValidationViolation]) -> None:
        content_type = content.get("content_type", "")
        if not content_type:
            return
        valid_types = {"social_post", "article", "news", "announcement", "poll", "story", "comparison", "tutorial", "guide", "tips", "quote", "pipeline"}
        if content_type not in valid_types:
            violations.append(ValidationViolation(field="content_type", severity=ViolationSeverity.WARNING, message=f"Unknown content_type: '{content_type}'", rule="valid_content_type"))

    def _validate_media_required(self, content: dict, violations: list[ValidationViolation]) -> None:
        if not content.get("media_required", False):
            violations.append(ValidationViolation(field="media_required", severity=ViolationSeverity.WARNING, message="Media is required by brand config but content doesn't request it", rule="media_required"))

    def _validate_platform_constraints(self, content: dict, platform: str, violations: list[ValidationViolation]) -> None:
        platforms_field = content.get("platforms", [])
        if platforms_field and isinstance(platforms_field, list) and platform not in platforms_field:
            violations.append(ValidationViolation(field="platforms", severity=ViolationSeverity.INFO, message=f"Content not explicitly targeted for '{platform}'", rule="platform_targeting"))

    def _validate_brand_constraints(self, content: dict, brand: Any, violations: list[ValidationViolation]) -> None:
        prohibited = getattr(brand, "prohibited_topics", []) or []
        caption = str(content.get("caption", "")).lower()
        title = str(content.get("title", "")).lower()
        for topic in prohibited:
            if topic.lower() in caption or topic.lower() in title:
                violations.append(ValidationViolation(field="caption", severity=ViolationSeverity.ERROR, message=f"Content contains prohibited topic: '{topic}'", rule="brand_prohibited_topic"))
        required_hashtags = getattr(brand, "required_hashtags", []) or []
        content_hashtags = content.get("hashtags", []) or []
        for tag in required_hashtags:
            if tag not in content_hashtags:
                violations.append(ValidationViolation(field="hashtags", severity=ViolationSeverity.WARNING, message=f"Missing required brand hashtag: #{tag}", rule="brand_required_hashtag"))

    # ═══════════════════════════════════════════════════════════════
    # HEALTH & STATISTICS (preserved)
    # ═══════════════════════════════════════════════════════════════

    def get_stats(self) -> dict:
        return {
            "validated_count": self._validated_count,
            "rejected_count": self._rejected_count,
            "rejection_rate": round(self._rejected_count / max(self._validated_count, 1) * 100, 1),
            "recent_posts_cached": len(self._recent_captions),
        }

    def health_check(self) -> dict:
        stats = self.get_stats()
        status = "healthy"
        if stats["rejection_rate"] > 50 and stats["validated_count"] > 10:
            status = "degraded"
        return {"status": status, "stats": stats}


# ═══════════════════════════════════════════════════════════════════════════
# MODULE SINGLETON
# ═══════════════════════════════════════════════════════════════════════════

content_validator = ContentValidator()


__all__ = [
    "ContentValidator",
    "ValidationStatus",
    "ValidationViolation",
    "ValidationResult",
    "ViolationSeverity",
    "content_validator",
]
