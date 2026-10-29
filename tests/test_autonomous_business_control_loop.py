from models.autonomous_business_control_loop import AutonomousBusinessControlLoop, ControlLoopObservation, LoopDisposition, LoopMode
from services.autonomous_business_control_loop import build_cycle, evaluate_control_loop


def loop(**kw):
    base = dict(workspace_id="ws-1", loop_id="loop-1", objective="keep service healthy", enabled=True, mode=LoopMode.GOVERNED_AUTONOMY, allowed_actions=("restart_service",))
    base.update(kw); return AutonomousBusinessControlLoop(**base)


def obs(ws="ws-1", confidence=.9, drift=False):
    return ControlLoopObservation(ws, "sig-1", "degraded", confidence, drift, ("e1",), "authoritative")


def test_disabled_loop_blocks():
    result = evaluate_control_loop(loop(enabled=False), (obs(),), proposed_action="restart_service")
    assert result.disposition == LoopDisposition.BLOCK


def test_workspace_isolation_blocks():
    result = evaluate_control_loop(loop(), (obs("other"),), proposed_action="restart_service")
    assert result.disposition == LoopDisposition.BLOCK
    assert "workspace_mismatch" in result.reasons


def test_low_confidence_does_not_autonomously_proceed():
    result = evaluate_control_loop(loop(), (obs(confidence=.2),), proposed_action="restart_service")
    assert result.disposition == LoopDisposition.READY_FOR_EXISTING_GOVERNANCE
    assert "low_confidence" in result.reasons


def test_unallowlisted_action_is_blocked():
    result = evaluate_control_loop(loop(), (obs(),), proposed_action="change_billing")
    assert result.disposition == LoopDisposition.BLOCK
    assert "action_not_allowlisted" in result.reasons


def test_repeated_failures_escalate_and_cycle_never_creates_intent():
    l = loop(max_consecutive_failures=2)
    cycle = build_cycle(l, (obs(drift=True),), proposed_action="restart_service", consecutive_failures=2)
    assert cycle.disposition == LoopDisposition.ESCALATE
    assert cycle.human_takeover_required is True
    assert cycle.execution_requested is False
    assert cycle.action_intent_created is False
    assert cycle.authority == "none"
