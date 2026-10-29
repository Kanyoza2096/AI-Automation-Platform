import pytest
from services.v68_15_learning_tenant_integrity import (
    assert_learning_signal_workspace,
    assert_decision_context_learning_workspace,
    assert_learning_context_integrity,
)


def learning(ws="w1", kid="k1"):
    return {"knowledge_id": kid, "workspace_id": ws, "authority": "none",
            "evidence": {"source_signal_ids": ["s1"]}}


def signal(ws="w1"):
    return {"signal_id": "s1", "workspace_id": ws}


def test_learning_evidence_must_stay_in_tenant():
    with pytest.raises(ValueError, match="workspace"):
        assert_learning_signal_workspace(learning(), [signal("w2")])


def test_unknown_learning_evidence_fails_closed():
    with pytest.raises(ValueError, match="unknown"):
        assert_learning_signal_workspace(learning(), [])


def test_same_tenant_learning_evidence_is_valid():
    assert_learning_signal_workspace(learning(), [signal()])


def test_decision_context_rejects_cross_tenant_learning():
    with pytest.raises(ValueError, match="workspace"):
        assert_decision_context_learning_workspace({"workspace_id": "w1"}, [learning("w2")])


def test_decision_context_rejects_authority_in_learning():
    with pytest.raises(ValueError, match="authority"):
        assert_decision_context_learning_workspace({"workspace_id": "w1"}, [dict(learning(), authority="execute")])


def test_decision_context_same_tenant_learning_is_valid():
    assert_decision_context_learning_workspace({"workspace_id": "w1"}, [learning()])


def test_projected_context_cannot_reference_unknown_knowledge():
    with pytest.raises(ValueError, match="unknown"):
        assert_learning_context_integrity({"workspace_id": "w1", "knowledge": [{"knowledge_id": "k1"}]}, [])


def test_projected_context_cannot_cross_tenants():
    with pytest.raises(ValueError, match="workspace"):
        assert_learning_context_integrity({"workspace_id": "w1", "knowledge": [{"knowledge_id": "k1"}]}, [learning("w2")])
