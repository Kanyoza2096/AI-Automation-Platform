from datetime import datetime, timedelta, timezone
import pytest
from services.business_learning_governance import (
    evaluate_learning_governance, decay_learning, validate_governed_learning,
)


def signal(i, state="positive", confidence=.9, created_at=None):
    return {
        "schema_version": "1.0", "signal_id": f"sig-{i}", "workspace_id": "w1",
        "target_type": "action", "target_id": "a1", "learning_state": state,
        "confidence": confidence, "authority": "none",
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
    }


def test_repeated_consistent_evidence_promotes():
    result = evaluate_learning_governance([signal(i) for i in range(3)])[0]
    assert result["promotion_state"] == "promoted"
    assert result["support_count"] == 3
    assert result["authority"] == "none"


def test_insufficient_support_remains_candidate():
    result = evaluate_learning_governance([signal(1), signal(2)])[0]
    assert result["promotion_state"] == "candidate"


def test_conflicting_evidence_is_not_promoted():
    result = evaluate_learning_governance([signal(1), signal(2), signal(3, "negative")])[0]
    assert result["promotion_state"] == "contradicted"
    assert result["direction"] == "positive"


def test_even_conflict_is_mixed():
    result = evaluate_learning_governance([signal(1), signal(2, "negative")])[0]
    assert result["promotion_state"] == "contradicted"
    assert result["direction"] == "mixed"


def test_stale_evidence_cannot_promote():
    old = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
    result = evaluate_learning_governance([signal(i, created_at=old) for i in range(3)])[0]
    assert result["promotion_state"] == "stale"


def test_uncertain_learning_is_not_promoted():
    result = evaluate_learning_governance([signal(1, "uncertain"), signal(2, "insufficient_evidence")])
    assert result == []


def test_low_confidence_remains_candidate():
    result = evaluate_learning_governance([signal(i, confidence=.5) for i in range(3)])[0]
    assert result["promotion_state"] == "candidate"


def test_decay_reduces_confidence_without_granting_authority():
    result = evaluate_learning_governance([signal(i) for i in range(3)])[0]
    decayed = decay_learning(result, age_days=30)
    assert decayed["confidence"] < result["confidence"]
    assert decayed["authority"] == "none"


def test_authority_tampering_fails_closed():
    result = evaluate_learning_governance([signal(i) for i in range(3)])[0]
    result["authority"] = "execute"
    with pytest.raises(ValueError):
        validate_governed_learning(result)
