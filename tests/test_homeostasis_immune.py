from datetime import datetime, timezone

from core.organism_state import Confidence, OperatingMode, OrganismState
from organism.homeostasis import HomeostaticAction, assess_homeostasis
from organism.immune import ImmuneAction, ThreatClass, detect_immune_signals


def state(**kwargs):
    base = dict(workspace_id="ws-1", updated_at=datetime.now(timezone.utc))
    base.update(kwargs)
    return OrganismState(**base)


def test_homeostasis_prioritizes_security_and_topology():
    proposals = assess_homeostasis(state(security_posture=.2, topology_healthy=False))
    assert proposals[0].action is HomeostaticAction.STABILIZE_DEPENDENCIES
    assert any(p.action is HomeostaticAction.REQUEST_REVIEW for p in proposals)


def test_homeostasis_continue_when_stable():
    proposals = assess_homeostasis(state())
    assert len(proposals) == 1
    assert proposals[0].action is HomeostaticAction.CONTINUE


def test_immune_quarantines_low_security():
    signals = detect_immune_signals(state(security_posture=.2))
    assert signals[0].threat is ThreatClass.SECURITY_DEGRADATION
    assert signals[0].action is ImmuneAction.QUARANTINE


def test_immune_detects_dependency_failure():
    signals = detect_immune_signals(state(topology_healthy=False))
    assert signals[0].threat is ThreatClass.DEPENDENCY_FAILURE


def test_immune_detects_high_risk_low_confidence():
    signals = detect_immune_signals(state(risk=.9, confidence=Confidence.LOW))
    assert any(s.action is ImmuneAction.ESCALATE for s in signals)
