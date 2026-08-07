"""
Tests for ContentValidator — Architecture §21-22, §44-45.

Validates:
  - Empty captions are rejected
  - Caption length limits per platform
  - Hashtag count limits
  - Brand prohibited topics
  - Brand required hashtags
  - Approved, ApprovedWithWarnings, Rejected states
"""

import pytest
from services.content_validator import (
    ContentValidator,
    ValidationResult,
    ValidationStatus,
    ValidationViolation,
    ViolationSeverity,
)


class TestContentValidatorBasic:
    """Basic validation of content structure."""

    def test_empty_caption_is_rejected(self):
        validator = ContentValidator()
        result = validator.validate(
            content={"caption": ""},
            platform="facebook",
        )

        assert result.is_approved is False
        assert result.is_rejected is True
        assert result.errors_count >= 1
        assert any("empty" in v.message.lower() for v in result.violations)

    def test_valid_content_is_approved(self):
        validator = ContentValidator()
        result = validator.validate(
            content={
                "caption": "This is a valid post about technology",
                "title": "Tech Update",
                "hashtags": ["tech", "ai"],
                "content_type": "social_post",
            },
            platform="facebook",
        )

        assert result.is_approved is True
        assert result.is_rejected is False

    def test_missing_caption_key_is_rejected(self):
        validator = ContentValidator()
        result = validator.validate(
            content={"title": "No Caption Here"},
            platform="facebook",
        )

        assert result.is_rejected is True


class TestContentValidatorPlatformLimits:
    """Platform-specific length limits."""

    def test_facebook_caption_within_limit(self):
        validator = ContentValidator()
        caption = "A" * 1000  # Well within 63206 limit
        result = validator.validate(
            content={"caption": caption},
            platform="facebook",
        )

        assert result.is_approved is True

    def test_facebook_caption_exceeds_limit(self):
        validator = ContentValidator()
        caption = "A" * 70000  # Exceeds 63206
        result = validator.validate(
            content={"caption": caption},
            platform="facebook",
        )

        assert result.is_rejected is True
        assert any("too long" in v.message.lower() for v in result.violations)

    def test_twitter_caption_280_limit(self):
        validator = ContentValidator()
        result = validator.validate(
            content={"caption": "A" * 300},
            platform="twitter",
        )

        assert result.is_rejected is True

    def test_approaching_limit_generates_warning(self):
        validator = ContentValidator()
        # ~90% of Facebook limit
        caption = "A" * 57000
        result = validator.validate(
            content={"caption": caption},
            platform="facebook",
        )

        # Should have a warning about approaching limit
        warnings = [v for v in result.violations if v.severity == ViolationSeverity.WARNING]
        assert len(warnings) >= 1
        assert any("approaching" in w.message.lower() for w in warnings)


class TestContentValidatorHashtags:
    """Hashtag validation."""

    def test_too_many_hashtags_warns(self):
        validator = ContentValidator()
        result = validator.validate(
            content={
                "caption": "Valid post",
                "hashtags": [f"tag{i}" for i in range(35)],  # > 30 limit
            },
            platform="facebook",
        )

        warnings = [v for v in result.violations if v.severity == ViolationSeverity.WARNING]
        assert any("hashtag" in w.message.lower() for w in warnings)

    def test_hashtags_not_list_is_error(self):
        validator = ContentValidator()
        result = validator.validate(
            content={
                "caption": "Valid post",
                "hashtags": "#tag1 #tag2",  # String, not list
            },
            platform="facebook",
        )

        assert result.is_rejected is True


class TestContentValidatorBrandConstraints:
    """Brand-specific validation rules."""

    def test_prohibited_topic_rejected(self):
        """Architecture §24 — Brand prohibited topics."""
        # Mock brand with prohibited topics
        brand = type("Brand", (), {
            "prohibited_topics": ["politics", "religion"],
            "required_hashtags": [],
        })()

        validator = ContentValidator()
        result = validator.validate(
            content={
                "caption": "Let's discuss politics today",
                "title": "Political Analysis",
            },
            brand=brand,
            platform="facebook",
        )

        assert result.is_rejected is True
        assert any("prohibited" in v.message.lower() for v in result.violations)

    def test_missing_required_hashtag_warns(self):
        """Architecture §24 — Brand required hashtags."""
        brand = type("Brand", (), {
            "prohibited_topics": [],
            "required_hashtags": ["kanyoza", "ai"],
        })()

        validator = ContentValidator()
        result = validator.validate(
            content={
                "caption": "Great new feature",
                "hashtags": ["tech"],  # Missing #kanyoza and #ai
            },
            brand=brand,
            platform="facebook",
        )

        warnings = [v for v in result.violations if v.severity == ViolationSeverity.WARNING]
        assert any("required" in w.message.lower() for w in warnings)


class TestValidationResult:
    """Test ValidationResult dataclass."""

    def test_approved_factory(self):
        result = ValidationResult.approved()
        assert result.is_approved is True
        assert result.is_rejected is False
        assert result.errors_count == 0
        assert result.warnings_count == 0

    def test_rejected_factory(self):
        violations = [
            ValidationViolation(
                field="caption",
                severity=ViolationSeverity.ERROR,
                message="Caption too long",
                rule="max_length",
            ),
            ValidationViolation(
                field="hashtags",
                severity=ViolationSeverity.WARNING,
                message="Too many hashtags",
                rule="max_hashtags",
            ),
        ]
        result = ValidationResult.rejected(violations)

        assert result.is_rejected is True
        assert result.errors_count == 1
        assert result.warnings_count == 1

    def test_to_dict(self):
        result = ValidationResult.approved()
        d = result.to_dict()

        assert d["status"] == "approved"
        assert d["approved"] is True
        assert d["errors_count"] == 0
