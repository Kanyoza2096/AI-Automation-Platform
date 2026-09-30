from services.business_learning_effectiveness import (
    build_learning_effectiveness,
    validate_learning_effectiveness,
)


def knowledge(**overrides):
    value = {
        "schema_version": "1.0", "knowledge_id": "k1", "workspace_id": "w1",
        "target_type": "action", "target_id": "crm_schedule_followup",
        "promotion_state": "promoted", "direction": "positive", "confidence": .9,
        "support_count": 5, "consistency": .8, "evidence": {},
        "lesson": "advisory lesson", "authority": "none",
    }
    value.update(overrides)
    return value


def feedback(success, oid, confidence=.9, **overrides):
    value = {
        "workspace_id": "w1", "knowledge_id": "k1", "outcome_observation_id": oid,
        "outcome_state": "succeeded" if success else "failed",
        "business_success": success, "confidence": confidence, "authority": "none",
    }
    value.update(overrides)
    return value


def test_effectiveness_requires_explicit_knowledge_feedback():
    result = build_learning_effectiveness(
        knowledge(), [feedback(True, "o1"), feedback(True, "o2"), feedback(True, "o3")],
        baseline_success_rate=.5, baseline_sample_count=6,
    )
    assert result["evaluation_state"] == "effective"
    assert result["lift"] == .5
    assert result["causal_claim"] == "association_only"
    validate_learning_effectiveness(result)


def test_without_baseline_does_not_claim_effectiveness():
    result = build_learning_effectiveness(
        knowledge(), [feedback(True, "o1"), feedback(True, "o2"), feedback(True, "o3")]
    )
    assert result["evaluation_state"] == "review_required"
    assert result["lift"] is None


def test_insufficient_samples_do_not_promote_effectiveness():
    result = build_learning_effectiveness(
        knowledge(), [feedback(True, "o1"), feedback(True, "o2")],
        baseline_success_rate=.2, baseline_sample_count=5,
    )
    assert result["evaluation_state"] == "insufficient_evidence"


def test_mixed_outcomes_require_review():
    result = build_learning_effectiveness(
        knowledge(), [feedback(True, "o1"), feedback(False, "o2"), feedback(True, "o3")],
        baseline_success_rate=.3, baseline_sample_count=6,
    )
    assert result["evaluation_state"] == "mixed"


def test_uncertain_outcomes_reduce_effective_evidence():
    result = build_learning_effectiveness(
        knowledge(), [feedback(True, "o1"), feedback(True, "o2"), feedback(True, "o3"),
                      feedback(None, "o4", outcome_state="unknown")],
        baseline_success_rate=.5, baseline_sample_count=6,
    )
    assert result["uncertain_count"] == 1
    assert result["evaluation_state"] in {"effective", "review_required"}


def test_cross_workspace_feedback_fails_closed():
    try:
        build_learning_effectiveness(knowledge(), [feedback(True, "o1", workspace_id="w2")])
    except ValueError as exc:
        assert "workspace" in str(exc)
    else:
        raise AssertionError("cross-workspace feedback must fail closed")


def test_non_promoted_learning_cannot_be_evaluated():
    try:
        build_learning_effectiveness(knowledge(promotion_state="candidate"), [])
    except ValueError as exc:
        assert "promoted" in str(exc)
    else:
        raise AssertionError("candidate learning must not be evaluated")


def test_authority_tampering_fails_closed():
    try:
        build_learning_effectiveness(knowledge(authority="execute"), [])
    except ValueError as exc:
        assert "authority" in str(exc)
    else:
        raise AssertionError("authority must remain none")
