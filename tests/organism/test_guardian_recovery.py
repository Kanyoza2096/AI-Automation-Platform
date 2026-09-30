import asyncio

from core.decision_gate import DecisionGate
from services.recovery_guardian import GuardianRecovery


def test_high_blast_radius_requires_approval():
    g = GuardianRecovery(decision_gate=DecisionGate())
    result = asyncio.run(g.recover(
        workspace_id="ws", action="restart_failed_service", target="redis",
        reason="critical dependency", correlation_id="c1",
        executor=lambda p: {"ok": True}, verifier=lambda p: True,
        blast_radius=8,
    ))
    assert result["status"] == "approval_required"


def test_low_risk_requires_verification_before_success(monkeypatch):
    monkeypatch.setattr("services.recovery_guardian.authority_from_workspace", lambda _: type("A", (), {"value": "AUTO_LOW"})())
    g = GuardianRecovery(decision_gate=DecisionGate())
    result = asyncio.run(g.recover(
        workspace_id="ws", action="refresh_health_state", target="meta",
        reason="stale health cache", correlation_id="c2",
        executor=lambda p: {"ok": True}, verifier=None,
    ))
    assert result["status"] == "verification_required"


def test_recovery_success_requires_positive_verification(monkeypatch):
    monkeypatch.setattr("services.recovery_guardian.authority_from_workspace", lambda _: type("A", (), {"value": "AUTO_LOW"})())
    g = GuardianRecovery(decision_gate=DecisionGate())
    result = asyncio.run(g.recover(
        workspace_id="ws", action="refresh_health_state", target="meta",
        reason="stale health cache", correlation_id="c3",
        executor=lambda p: {"ok": True}, verifier=lambda p: True,
    ))
    assert result["status"] == "recovered"
    assert result["decision_id"]


def test_recovery_is_bounded_and_cooldown_protects_repeat_execution(monkeypatch):
    monkeypatch.setattr("services.recovery_guardian.authority_from_workspace", lambda _: type("A", (), {"value": "AUTO_LOW"})())
    now = [100.0]
    g = GuardianRecovery(decision_gate=DecisionGate(), clock=lambda: now[0])
    kwargs = dict(
        workspace_id="ws", action="refresh_health_state", target="meta",
        reason="test", correlation_id="same",
        executor=lambda p: {"ok": True}, verifier=lambda p: True,
    )
    first = asyncio.run(g.recover(**kwargs))
    second = asyncio.run(g.recover(**kwargs))
    assert first["status"] == "recovered"
    assert second["status"] == "cooldown"


def test_unknown_recovery_action_fails_closed():
    g = GuardianRecovery(decision_gate=DecisionGate())
    try:
        asyncio.run(g.recover(
            workspace_id="ws", action="drop_database", target="prod",
            reason="x", correlation_id="c4", executor=lambda p: {"ok": True}, verifier=lambda p: True,
        ))
    except Exception as exc:
        assert "unsupported recovery action" in str(exc)
    else:
        raise AssertionError("unsafe recovery action was accepted")
