"""Regression tests for evidence-backed quantitative content claims."""
from services.content_engine import ContentEngine

def test_quantitative_performance_claim_without_evidence_is_rejected():
    caption = ("We saw a 30% reduction in agent restarts after idempotent task handling. "
               "Average task latency fell from 1200ms to 900ms.")
    ok, reason = ContentEngine._validate_factual_claims(caption)
    assert not ok
    assert "verified" in reason

def test_unverified_knowledge_text_is_not_accepted_as_measurement_evidence():
    caption = "Our latency dropped from 250ms to 120ms after switching to JWT."
    ok, reason = ContentEngine._validate_factual_claims(
        caption, {"text": "Benchmark measured 250ms and 120ms.", "verified": False, "source_id": "kb-1"})
    assert not ok
    assert "verified" in reason

def test_quantitative_claim_requires_traceable_verified_source_and_matching_values():
    caption = "Our latency dropped from 250ms to 120ms after switching to JWT."
    ok, reason = ContentEngine._validate_factual_claims(
        caption, {"text": "A verified benchmark measured latency at 250ms and 120ms.",
                  "verified": True, "source_id": "benchmark-2026-04"})
    assert ok, reason
    ok, reason = ContentEngine._validate_factual_claims(
        caption, {"text": "A verified benchmark measured latency at 250ms.",
                  "verified": True, "source_id": "benchmark-2026-04"})
    assert not ok
    assert "absent from verified evidence" in reason

def test_general_technical_advice_does_not_require_numeric_evidence():
    caption = ("Idempotent task handlers make retries safer because repeated execution "
               "does not duplicate side effects. Persist a stable task key before retrying.")
    assert ContentEngine._validate_factual_claims(caption) == (
        True, "no empirical performance claim detected")
