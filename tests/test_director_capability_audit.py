"""Regression tests for Director capability binding policy."""

from types import SimpleNamespace

from services.director_bindings import (
    INTENTIONALLY_UNBOUND_CAPABILITIES,
    assert_no_unbound_capabilities,
)


def _cap(handler):
    return SimpleNamespace(handler=handler)


def test_scheduled_publishing_is_intentionally_excluded_from_binding_audit():
    director = SimpleNamespace(
        capabilities={
            "publish_scheduled_content": _cap(lambda params: None),
            "review_content_quality": _cap(lambda params: None),
        }
    )

    assert "publish_scheduled_content" in INTENTIONALLY_UNBOUND_CAPABILITIES
    assert assert_no_unbound_capabilities(director) == ["review_content_quality"]


def test_audit_still_reports_unintended_placeholder_handlers():
    def real_handler(params):
        return {"ok": True}

    def placeholder_handler(params):
        return None

    director = SimpleNamespace(
        capabilities={
            "healthy_capability": _cap(real_handler),
            "broken_capability": _cap(placeholder_handler),
        }
    )

    assert assert_no_unbound_capabilities(director) == ["broken_capability"]
