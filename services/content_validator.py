"""
Content Validator — Architecture §21-22.

Validates AI-generated content before it reaches the renderer or publisher.
Ensures structured output meets brand constraints, platform requirements,
and content policies BEFORE the system commits to rendering or posting.

Architecture §44 — AI Reliability:
  "AI output should never be assumed to be valid simply because the
   model returned a response."
  
  Gemini output → Validation → Approved structured content
  NOT: Gemini output → Facebook

Architecture §45 — Structured AI Output Validation:
  Validates: title, caption, content_type, hashtags, media requirements,
  platform requirements, length, brand constraints.
  Rejects malformed responses. Does not silently accept partial AI responses.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from core.errors import AIValidationError, ErrorCode

logger = logging.getLogger("kanyoza.services.content_validator")


# ═══════════════════════════════════════════════════════════════════════════
# Validation Status
# ═══════════════════════════════════════════════════════════════════════════


class ValidationStatus(str, Enum):
    """Outcome of content validation."""
    APPROVED = "approved"              # Content passed all checks
    APPROVED_WITH_WARNINGS = "approved_with_warnings"  # Minor issues, still publishable
    REJECTED = "rejected"              # Fatal issues, cannot publish
    NEEDS_REVIEW = "needs_review"      # Flagged for human review


# ═══════════════════════════════════════════════════════════════════════════
# Validation Violation
# ═══════════════════════════════════════════════════════════════════════════


class ViolationSeverity(str, Enum):
    """How severe a validation violation is."""
    ERROR = "error"        # Fatal — content cannot be published
    WARNING = "warning"    # Publishable but degraded
    INFO = "info"          # Informational only


@dataclass
class ValidationViolation:
    """A single validation issue found in content."""
    field: str                    # Which field has the issue
    severity: ViolationSeverity   # How severe
    message: str                  # Human-readable description
    rule: str = ""                # Which validation rule was violated
    
    @property
    def is_fatal(self) -> bool:
        return self.severity == ViolationSeverity.ERROR


# ═══════════════════════════════════════════════════════════════════════════
# Validation Result
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class ValidationResult:
    """Complete result of content validation.
    
    Architecture §28 — Why None Is Not an Error Model:
      Every validation produces a structured result, never None.
      The caller always knows WHAT failed, WHERE, and WHY.
    """
    status: ValidationStatus
    violations: list[ValidationViolation] = field(default_factory=list)
    warnings_count: int = 0
    errors_count: int = 0
    
    @property
    def is_approved(self) -> bool:
        """True if content can proceed to rendering/publishing."""
        return self.status in (ValidationStatus.APPROVED, ValidationStatus.APPROVED_WITH_WARNINGS)
    
    @property
    def is_rejected(self) -> bool:
        """True if content has fatal issues."""
        return self.status == ValidationStatus.REJECTED
    
    @property
    def needs_review(self) -> bool:
        """True if content should be reviewed by a human."""
        return self.status == ValidationStatus.NEEDS_REVIEW
    
    @property
    def error_messages(self) -> list[str]:
        """List of error-level violation messages."""
        return [v.message for v in self.violations if v.severity == ViolationSeverity.ERROR]
    
    @property
    def warning_messages(self) -> list[str]:
        """List of warning-level violation messages."""
        return [v.message for v in self.violations if v.severity == ViolationSeverity.WARNING]
    
    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "approved": self.is_approved,
            "errors_count": self.errors_count,
            "warnings_count": self.warnings_count,
            "violations": [
                {
                    "field": v.field,
                    "severity": v.severity.value,
                    "message": v.message,
                    "rule": v.rule,
                }
                for v in self.violations
            ],
        }
    
    @classmethod
    def approved(cls) -> "ValidationResult":
        """Factory for clean validation."""
        return cls(status=ValidationStatus.APPROVED)
    
    @classmethod
    def rejected(cls, violations: list[ValidationViolation]) -> "ValidationResult":
        """Factory for failed validation."""
        errors = sum(1 for v in violations if v.severity == ViolationSeverity.ERROR)
        warnings = sum(1 for v in violations if v.severity == ViolationSeverity.WARNING)
        return cls(
            status=ValidationStatus.REJECTED,
            violations=violations,
            errors_count=errors,
            warnings_count=warnings,
        )


# ═══════════════════════════════════════════════════════════════════════════
# Platform-specific limits
# ═══════════════════════════════════════════════════════════════════════════

# These should eventually move to per-platform configuration,
# but for now centralizing them here prevents magic numbers
# scattered across the codebase.

PLATFORM_LIMITS: dict[str, dict[str, int]] = {
    "facebook": {
        "max_caption_length": 63206,     # Facebook post character limit
        "max_hashtags": 30,              # Practical limit, not hard enforced
        "max_title_length": 200,         # Recommended title length
        "min_caption_length": 1,         # Cannot publish empty post
    },
    "instagram": {
        "max_caption_length": 2200,
        "max_hashtags": 30,
        "max_title_length": 100,
        "min_caption_length": 1,
    },
    "linkedin": {
        "max_caption_length": 3000,
        "max_hashtags": 5,
        "max_title_length": 200,
        "min_caption_length": 1,
    },
    "twitter": {
        "max_caption_length": 280,
        "max_hashtags": 10,
        "max_title_length": 100,
        "min_caption_length": 1,
    },
}


# ═══════════════════════════════════════════════════════════════════════════
# Content Policy — prohibited patterns
# ═══════════════════════════════════════════════════════════════════════════

# These patterns indicate problematic content.
# In production, this should be configurable per brand and
# potentially use AI-based content policy detection.

PROHIBITED_PATTERNS: list[tuple[str, str]] = [
    # (pattern, description)
    # Add brand-specific prohibited patterns here
    # Example: ("competitor_name", "Mention of competitor brand"),
]


# ═══════════════════════════════════════════════════════════════════════════
# Content Validator
# ═══════════════════════════════════════════════════════════════════════════


class ContentValidator:
    """Validates AI-generated structured content before publishing.
    
    Usage:
        validator = ContentValidator()
        result = validator.validate(
            content={"title": "...", "caption": "...", "hashtags": [...]},
            brand=brand,
            platform="facebook",
        )
        
        if result.is_approved:
            proceed_to_render(content)
        else:
            handle_validation_failure(result)
    """
    
    def __init__(self) -> None:
        self._limits = PLATFORM_LIMITS
        self._prohibited = PROHIBITED_PATTERNS
    
    def validate(
        self,
        content: dict[str, Any],
        brand: Optional[Any] = None,
        platform: str = "facebook",
        require_media: bool = False,
    ) -> ValidationResult:
        """Validate structured content against all applicable rules.
        
        Args:
            content: Dict with keys: title, caption, content_type, hashtags,
                    media_required, metadata
            brand: Brand model for brand-specific validation
            platform: Target platform ("facebook", "instagram", etc.)
            require_media: If True, content must indicate media is needed
        
        Returns:
            ValidationResult with status and list of violations
        """
        violations: list[ValidationViolation] = []
        limits = self._limits.get(platform, self._limits["facebook"])
        
        # ── Required fields ──────────────────────────────────────────
        self._check_required_fields(content, violations)
        
        # ── Caption validation ───────────────────────────────────────
        self._validate_caption(content, limits, violations)
        
        # ── Title validation ─────────────────────────────────────────
        self._validate_title(content, limits, violations)
        
        # ── Hashtag validation ───────────────────────────────────────
        self._validate_hashtags(content, limits, violations)
        
        # ── Content type validation ──────────────────────────────────
        self._validate_content_type(content, violations)
        
        # ── Media requirement validation ─────────────────────────────
        if require_media:
            self._validate_media_required(content, violations)
        
        # ── Platform constraint validation ───────────────────────────
        self._validate_platform_constraints(content, platform, violations)
        
        # ── Brand-specific validation ────────────────────────────────
        if brand:
            self._validate_brand_constraints(content, brand, violations)
        
        # ── Prohibited content check ─────────────────────────────────
        self._check_prohibited_patterns(content, violations)
        
        # ── Determine overall status ─────────────────────────────────
        errors = [v for v in violations if v.severity == ViolationSeverity.ERROR]
        warnings = [v for v in violations if v.severity == ViolationSeverity.WARNING]
        
        if errors:
            status = ValidationStatus.REJECTED
        elif warnings:
            status = ValidationStatus.APPROVED_WITH_WARNINGS
        else:
            status = ValidationStatus.APPROVED
        
        result = ValidationResult(
            status=status,
            violations=violations,
            errors_count=len(errors),
            warnings_count=len(warnings),
        )
        
        if not result.is_approved:
            logger.warning(
                "Content validation REJECTED: %d errors, %d warnings",
                result.errors_count, result.warnings_count,
            )
            for v in errors:
                logger.warning("  [ERROR] %s: %s", v.field, v.message)
        
        return result
    
    # ═══════════════════════════════════════════════════════════════════
    # Individual validation checks
    # ═══════════════════════════════════════════════════════════════════
    
    def _check_required_fields(
        self, content: dict, violations: list[ValidationViolation]
    ) -> None:
        """Ensure content has the minimum required fields."""
        if not content.get("caption") or not str(content.get("caption", "")).strip():
            violations.append(ValidationViolation(
                field="caption",
                severity=ViolationSeverity.ERROR,
                message="Caption is empty or missing",
                rule="required_field",
            ))
    
    def _validate_caption(
        self, content: dict, limits: dict, violations: list[ValidationViolation]
    ) -> None:
        """Validate caption length and content."""
        caption = str(content.get("caption", ""))
        max_length = limits.get("max_caption_length", 63206)
        min_length = limits.get("min_caption_length", 1)
        
        if len(caption) > max_length:
            violations.append(ValidationViolation(
                field="caption",
                severity=ViolationSeverity.ERROR,
                message=f"Caption too long: {len(caption)} chars (max {max_length})",
                rule="max_length",
            ))
        elif len(caption) < min_length:
            violations.append(ValidationViolation(
                field="caption",
                severity=ViolationSeverity.ERROR,
                message=f"Caption too short: {len(caption)} chars (min {min_length})",
                rule="min_length",
            ))
        
        # Warn if approaching the limit
        if len(caption) > max_length * 0.9:
            violations.append(ValidationViolation(
                field="caption",
                severity=ViolationSeverity.WARNING,
                message=f"Caption approaching limit: {len(caption)}/{max_length} chars",
                rule="approaching_max_length",
            ))
    
    def _validate_title(
        self, content: dict, limits: dict, violations: list[ValidationViolation]
    ) -> None:
        """Validate title if present."""
        title = str(content.get("title", ""))
        if not title:
            return  # Title is optional
        
        max_title = limits.get("max_title_length", 200)
        if len(title) > max_title:
            violations.append(ValidationViolation(
                field="title",
                severity=ViolationSeverity.WARNING,
                message=f"Title exceeds recommended length: {len(title)}/{max_title}",
                rule="max_title_length",
            ))
    
    def _validate_hashtags(
        self, content: dict, limits: dict, violations: list[ValidationViolation]
    ) -> None:
        """Validate hashtags count and format."""
        hashtags = content.get("hashtags", [])
        if not hashtags:
            return  # Hashtags are optional
        
        if not isinstance(hashtags, list):
            violations.append(ValidationViolation(
                field="hashtags",
                severity=ViolationSeverity.ERROR,
                message="Hashtags must be a list",
                rule="hashtags_type",
            ))
            return
        
        max_hashtags = limits.get("max_hashtags", 30)
        if len(hashtags) > max_hashtags:
            violations.append(ValidationViolation(
                field="hashtags",
                severity=ViolationSeverity.WARNING,
                message=f"Too many hashtags: {len(hashtags)} (max {max_hashtags})",
                rule="max_hashtags",
            ))
    
    def _validate_content_type(
        self, content: dict, violations: list[ValidationViolation]
    ) -> None:
        """Validate content_type if present."""
        content_type = content.get("content_type", "")
        if not content_type:
            return
        
        valid_types = {"social_post", "article", "news", "announcement", "poll", "story"}
        if content_type not in valid_types:
            violations.append(ValidationViolation(
                field="content_type",
                severity=ViolationSeverity.WARNING,
                message=f"Unknown content_type: '{content_type}'. Valid: {valid_types}",
                rule="valid_content_type",
            ))
    
    def _validate_media_required(
        self, content: dict, violations: list[ValidationViolation]
    ) -> None:
        """Ensure content indicates media is required when configured."""
        media_required = content.get("media_required", False)
        if not media_required:
            violations.append(ValidationViolation(
                field="media_required",
                severity=ViolationSeverity.WARNING,
                message="Media is required by brand config but content doesn't request it",
                rule="media_required",
            ))
    
    def _validate_platform_constraints(
        self, content: dict, platform: str, violations: list[ValidationViolation]
    ) -> None:
        """Platform-specific validation rules."""
        platforms_field = content.get("platforms", [])
        if platforms_field and isinstance(platforms_field, list):
            if platform not in platforms_field:
                violations.append(ValidationViolation(
                    field="platforms",
                    severity=ViolationSeverity.INFO,
                    message=f"Content not explicitly targeted for '{platform}'",
                    rule="platform_targeting",
                ))
    
    def _validate_brand_constraints(
        self, content: dict, brand: Any, violations: list[ValidationViolation]
    ) -> None:
        """Validate against brand-specific rules."""
        # Check for prohibited topics from brand config
        prohibited = getattr(brand, 'prohibited_topics', []) or []
        caption = str(content.get("caption", "")).lower()
        title = str(content.get("title", "")).lower()
        
        for topic in prohibited:
            if topic.lower() in caption or topic.lower() in title:
                violations.append(ValidationViolation(
                    field="caption",
                    severity=ViolationSeverity.ERROR,
                    message=f"Content contains prohibited topic: '{topic}'",
                    rule="brand_prohibited_topic",
                ))
        
        # Check brand-required hashtags
        required_hashtags = getattr(brand, 'required_hashtags', []) or []
        content_hashtags = content.get("hashtags", []) or []
        for tag in required_hashtags:
            if tag not in content_hashtags:
                violations.append(ValidationViolation(
                    field="hashtags",
                    severity=ViolationSeverity.WARNING,
                    message=f"Missing required brand hashtag: #{tag}",
                    rule="brand_required_hashtag",
                ))
    
    def _check_prohibited_patterns(
        self, content: dict, violations: list[ValidationViolation]
    ) -> None:
        """Check content against prohibited patterns."""
        caption = str(content.get("caption", "")).lower()
        title = str(content.get("title", "")).lower()
        full_text = f"{title} {caption}"
        
        for pattern, description in self._prohibited:
            if pattern.lower() in full_text:
                violations.append(ValidationViolation(
                    field="caption",
                    severity=ViolationSeverity.ERROR,
                    message=f"Prohibited content detected: {description}",
                    rule="prohibited_pattern",
                ))


# ═══════════════════════════════════════════════════════════════════════════
# Module-level instance
# ═══════════════════════════════════════════════════════════════════════════

content_validator = ContentValidator()
