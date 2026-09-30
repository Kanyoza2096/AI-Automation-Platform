import pytest

from core.decision_gate import DecisionGate, DecisionGateError, DecisionSource


def test_publish_cannot_use_decision_for_other_action():
    gate = DecisionGate()
    decision = gate.issue(
        source=DecisionSource.DIRECTOR_AUTO,
        workspace_id="ws-1",
        action_name="plugin_execute",
        approved=True,
        reason="approved",
    )
    with pytest.raises(DecisionGateError, match="not 'publish'"):
        gate.require_for_publish(decision.decision_id, workspace_id="ws-1")
