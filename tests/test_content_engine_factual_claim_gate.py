"""Regression tests for evidence-backed quantitative content claims."""
from services.content_engine import ContentEngine

def test_quantitative_performance_claim_without_evidence_is_rejected():
    caption = ("We saw a 30% reduction in agent restarts after idempotent task handling. "
               "Average task latency fell from 1200ms to 900ms.")
    ok, reason = ContentEngine._validate_factual_claims(caption)
    assert not ok
    assert "no verified evidence" in reason

def test_quantitative_performance_claim_requires_all_values_in_evidence():
    caption = "Our latency dropped from 250ms to 120ms after switching to JWT."
    ok, reason = ContentEngine._validate_factual_claims(
        caption, "A verified benchmark measured latency at 250ms and 120ms.")
    assert ok, reason
    ok, reason = ContentEngine._validate_factual_claims(
        caption, "A verified benchmark measured latency at 250ms.")
    assert not ok
    assert "absent from verified evidence" in reason

def test_general_technical_advice_does_not_require_numeric_evidence():
    caption = ("Idempotent task handlers make retries safer because repeated execution "
               "does not duplicate side effects. Persist a stable task key before retrying.")
    assert ContentEngine._validate_factual_claims(caption) == (
        True, "no empirical performance claim detected")
