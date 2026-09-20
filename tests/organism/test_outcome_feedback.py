import asyncio

from core.decision_gate import DecisionGate, DecisionSource
from core.outcome_feedback import record_action_outcome


def test_outcome_persists_mutated_decision(monkeypatch):
    gate = DecisionGate()
    calls = []
    monkeypatch.setattr(gate, "_persist", lambda d: calls.append((d.decision_id, d.outcome)))
    d = gate.issue(source=DecisionSource.DIRECTOR_AUTO, workspace_id="ws", action_name="publish", approved=True)
    calls.clear()
    gate.record_outcome(d.decision_id, outcome="success", score=1.0, source="system")
    assert calls == [(d.decision_id, "success")]


def test_outcome_feedback_rejects_workspace_mismatch():
    gate = DecisionGate()
    d = gate.issue(source=DecisionSource.DIRECTOR_AUTO, workspace_id="ws-a", action_name="publish", approved=True)
    import core.decision_gate as mod
    old = mod.decision_gate
    mod.decision_gate = gate
    try:
        try:
            asyncio.run(record_action_outcome(
                outcome="success", decision_id=d.decision_id, workspace_id="ws-b"
            ))
        except PermissionError:
            pass
        else:
            raise AssertionError("cross-workspace outcome was accepted")
    finally:
        mod.decision_gate = old


def test_intelligence_learns_verified_outcome_by_decision_id():
    from core.autonomous_loop import IntelligenceEngine, DecisionType
    from datetime import datetime, timezone

    engine = IntelligenceEngine()
    decision = type("D", (), {
        "decision_id": "dec-test", "action": "monitor", "rationale": "x",
        "confidence": 0.8,
    })()
    engine._decision_history.append({"decision": {"decision_id": "dec-test", "action": "monitor"}})
    before = engine._confidence
    assert engine.learn_by_decision_id("dec-test", outcome="success", success=True, score=1.0)
    assert engine._confidence > before
    assert engine._decision_history[-1]["success"] is True
