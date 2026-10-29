import pytest

from services.intelligence_learning_feedback import IntelligenceLearningFeedbackEngine, IntelligenceLearningFeedbackStore
from services.intelligence_learning_decision_context import (
    MAX_ITEMS,
    build_learning_decision_context,
    validate_learning_decision_context,
)


def _feedback(ws="ws-1", state="positive", fp="fp-1"):
    return {
        "schema_version": "KDO.IntelligenceLearningFeedback/v1",
        "workspace_id": ws,
        "feedback_id": "fb-1",
        "execution_id": "exe-1",
        "outcome_id": "out-1",
        "attribution_fingerprint": "attr-1",
        "learning_state": state,
        "measured_success": state == "positive" if state != "uncertain" else None,
        "confidence": 0.9 if state != "uncertain" else 0.2,
        "evidence_fingerprint": fp,
        "authority": "none",
        "causal_claim": False,
        "governance_mutation": False,
        "execution_authority": False,
    }


def test_learning_context_is_bounded_advisory_projection():
    context = build_learning_decision_context(
        workspace_id="ws-1",
        learning_feedback=[_feedback(fp=f"fp-{i}") for i in range(MAX_ITEMS + 3)],
    )
    value = context.to_dict()
    assert len(value["feedback"]) == MAX_ITEMS
    assert value["advisory_only"] is True
    assert value["authority"] == "none"
    assert value["policy_effect"] == "none"
    assert value["approval_effect"] == "none"
    assert value["autonomy_effect"] == "none"
    assert value["execution_effect"] == "none"
    assert value["action_override"] is None
    validate_learning_decision_context(value)


def test_workspace_isolation_is_fail_closed():
    with pytest.raises(PermissionError, match="workspace mismatch"):
        build_learning_decision_context(workspace_id="ws-1", learning_feedback=[_feedback("ws-2")])


def test_learning_context_deduplicates_evidence():
    context = build_learning_decision_context(
        workspace_id="ws-1", learning_feedback=[_feedback(), _feedback(fp="fp-1")]
    )
    assert len(context.feedback) == 1
    assert len(context.source_fingerprints) == 1


def test_uncertain_learning_remains_uncertain():
    context = build_learning_decision_context(
        workspace_id="ws-1", learning_feedback=[_feedback(state="uncertain", fp="uncertain")]
    )
    assert context.feedback[0]["learning_state"] == "uncertain"


def test_learning_cannot_supply_policy_or_action_override():
    value = build_learning_decision_context(workspace_id="ws-1", learning_feedback=[_feedback()]).to_dict()
    value["action_override"] = "security.contain_asset"
    with pytest.raises(ValueError, match="action_override"):
        validate_learning_decision_context(value)


def test_learning_cannot_grant_authority():
    value = build_learning_decision_context(workspace_id="ws-1", learning_feedback=[_feedback()]).to_dict()
    value["authority"] = "execution"
    with pytest.raises(ValueError, match="authority"):
        validate_learning_decision_context(value)


def test_learning_cannot_claim_causation_or_mutate_history():
    value = build_learning_decision_context(workspace_id="ws-1", learning_feedback=[_feedback()]).to_dict()
    value["causal_claim"] = True
    with pytest.raises(ValueError, match="causal"):
        validate_learning_decision_context(value)


def test_context_fingerprint_is_deterministic():
    rows = [_feedback()]
    a = build_learning_decision_context(workspace_id="ws-1", learning_feedback=rows)
    b = build_learning_decision_context(workspace_id="ws-1", learning_feedback=rows)
    assert a.context_id == b.context_id


def test_tampered_source_feedback_is_rejected():
    value = build_learning_decision_context(workspace_id="ws-1", learning_feedback=[_feedback()]).to_dict()
    value["feedback"][0]["governance_mutation"] = True
    with pytest.raises(ValueError, match="governance"):
        validate_learning_decision_context(value)


def test_real_learning_store_projects_into_decision_context(tmp_path):
    path = tmp_path / "learning.sqlite"
    feedback = IntelligenceLearningFeedbackEngine().derive(
        workspace_id="ws-1",
        attribution={
            "workspace_id": "ws-1", "execution_id": "exe-1", "outcome_id": "out-1",
            "evidence_fingerprint": "attr-1",
            "authoritative_outcome_source": "verification_and_reconciliation",
            "causal_claim": False, "action_authority_granted": False,
        },
        outcome_measurement={"measured_success": True, "confidence": 0.9},
    )
    store = IntelligenceLearningFeedbackStore(path)
    store.append(feedback)
    store.close()
    reopened = IntelligenceLearningFeedbackStore(path)
    context = build_learning_decision_context(
        workspace_id="ws-1", learning_feedback=reopened.load_workspace("ws-1")
    )
    validate_learning_decision_context(context.to_dict())
    assert context.feedback[0]["evidence_fingerprint"] == feedback.evidence_fingerprint
    assert context.feedback[0]["learning_state"] == "positive"
    reopened.close()
