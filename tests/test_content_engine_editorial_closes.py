"""Regression tests for non-assumptive editorial closing questions."""

from services.content_engine import ContentEngine


def _caption(closing_question: str) -> str:
    body = (
        "AI failure classification separates retrieval misses from reasoning "
        "errors, unsupported generation, invalid output formats, tool failures, "
        "authorization denials, policy violations, and infrastructure problems. "
        "Each category points to a different diagnostic path. Retrieval failures "
        "call for inspecting evidence coverage and query behavior, while format "
        "failures call for schema validation and deterministic checks. A useful "
        "trace connects the request, prompt version, evidence identifiers, tool "
        "calls, validation decisions, and final outcome while protecting "
        "sensitive data through redaction and retention controls."
    )
    return f"{body} {closing_question}"


def test_rejects_closing_question_that_assumes_production_implementation():
    ok, reason = ContentEngine._validate_caption_quality(
        _caption(
            "What specific strategies have you implemented to classify failures "
            "in your production environment?"
        )
    )
    assert not ok
    assert reason.startswith("assumptive_generic_close:")


def test_rejects_question_that_assumes_implementation_history():
    ok, reason = ContentEngine._validate_caption_quality(
        _caption("What strategies have you deployed to measure these outcomes?")
    )
    assert not ok
    assert reason.startswith("assumptive_generic_close:")


def test_allows_specific_question_about_a_technical_tradeoff():
    ok, reason = ContentEngine._validate_caption_quality(
        _caption(
            "Which trace field is most useful for distinguishing a retrieval "
            "miss from an unsupported generation?"
        )
    )
    assert ok, reason


def test_assumptive_question_in_body_does_not_trigger_final_close_rule():
    body = (
        "What strategies have you implemented in production? "
        + _caption(
            "Which evidence identifier would make this failure easiest to audit?"
        )
    )
    ok, reason = ContentEngine._validate_caption_quality(body)
    assert ok, reason
