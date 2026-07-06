"""
Tests for utils/security.py — webhook signature verification and message helpers.
"""

import hashlib
import hmac

import pytest

from utils.security import (
    verify_facebook_signature,
    sanitize_message,
    detect_abuse,
    mask_secret,
    is_safe_url,
    sanitize_filename,
)


# ---------------------------------------------------------------------------
# verify_facebook_signature
# ---------------------------------------------------------------------------


def _make_signature(payload: bytes, secret: str) -> str:
    """Helper — compute the correct sha256 HMAC for a payload."""
    digest = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


class TestVerifyFacebookSignature:
    def test_valid_signature_accepted(self):
        payload = b'{"object":"page"}'
        secret = "my-app-secret"
        sig = _make_signature(payload, secret)
        assert verify_facebook_signature(payload, sig, secret) is True

    def test_tampered_payload_rejected(self):
        secret = "my-app-secret"
        sig = _make_signature(b"original", secret)
        assert verify_facebook_signature(b"tampered", sig, secret) is False

    def test_wrong_secret_rejected(self):
        payload = b'{"object":"page"}'
        sig = _make_signature(payload, "correct-secret")
        assert verify_facebook_signature(payload, sig, "wrong-secret") is False

    def test_missing_sha256_prefix_rejected(self):
        payload = b"data"
        secret = "secret"
        raw_hex = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        # No "sha256=" prefix
        assert verify_facebook_signature(payload, raw_hex, secret) is False

    def test_empty_secret_always_returns_false(self):
        """
        Critical: an unconfigured APP_SECRET must NEVER allow requests through.
        verify_facebook_signature must return False — not True — when secret is empty.
        """
        payload = b'{"object":"page"}'
        assert verify_facebook_signature(payload, "sha256=anything", "") is False
        assert verify_facebook_signature(payload, "", "") is False

    def test_empty_signature_header_rejected(self):
        assert verify_facebook_signature(b"data", "", "secret") is False


# ---------------------------------------------------------------------------
# sanitize_message
# ---------------------------------------------------------------------------


class TestSanitizeMessage:
    def test_strips_control_characters(self):
        text = "hello\x00world\x01test"
        assert "\x00" not in sanitize_message(text)
        assert "\x01" not in sanitize_message(text)

    def test_collapses_whitespace(self):
        assert sanitize_message("hello   world") == "hello world"

    def test_strips_leading_trailing_whitespace(self):
        assert sanitize_message("  hi  ") == "hi"

    def test_caps_at_2000_chars(self):
        long_text = "a" * 5000
        assert len(sanitize_message(long_text)) == 2000

    def test_empty_string(self):
        assert sanitize_message("") == ""


# ---------------------------------------------------------------------------
# detect_abuse
# ---------------------------------------------------------------------------


class TestDetectAbuse:
    def test_clean_message_not_flagged(self):
        assert detect_abuse("Hello, how are you?") is False
        assert detect_abuse("Tell me about Python") is False

    def test_abusive_message_flagged(self):
        assert detect_abuse("you are so stupid") is True

    def test_case_insensitive(self):
        assert detect_abuse("STUPID BOT") is True


# ---------------------------------------------------------------------------
# mask_secret
# ---------------------------------------------------------------------------


class TestMaskSecret:
    def test_masks_most_of_secret(self):
        masked = mask_secret("ABCDEFGH", visible_chars=4)
        assert masked.endswith("EFGH")
        assert masked.startswith("****")

    def test_empty_returns_stars(self):
        assert mask_secret("") == "***"

    def test_short_value_fully_masked(self):
        result = mask_secret("AB", visible_chars=4)
        assert result == "**"


# ---------------------------------------------------------------------------
# is_safe_url
# ---------------------------------------------------------------------------


class TestIsSafeUrl:
    def test_https_accepted(self):
        assert is_safe_url("https://example.com/path") is True

    def test_http_rejected_by_default(self):
        assert is_safe_url("http://example.com") is False

    def test_javascript_scheme_rejected(self):
        assert is_safe_url("javascript:alert(1)") is False

    def test_empty_rejected(self):
        assert is_safe_url("") is False


# ---------------------------------------------------------------------------
# sanitize_filename
# ---------------------------------------------------------------------------


class TestSanitizeFilename:
    def test_removes_path_separators(self):
        assert "/" not in sanitize_filename("../../etc/passwd")
        assert "\\" not in sanitize_filename("..\\windows\\system32")

    def test_empty_becomes_unnamed(self):
        assert sanitize_filename("") == "unnamed"

    def test_normal_name_unchanged(self):
        assert sanitize_filename("report_2026.pdf") == "report_2026.pdf"
