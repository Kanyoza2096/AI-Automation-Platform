"""Regression tests for truthful reliability remediation planning."""

from services.reliability.models import RemediationAction
from services.reliability.planner import CAUSE_PLAYBOOK


def test_generic_service_degradation_does_not_schedule_unimplemented_refresh():
    assert CAUSE_PLAYBOOK["service_health_degraded"] is RemediationAction.ESCALATE_HUMAN
    assert CAUSE_PLAYBOOK["degraded_engines"] is RemediationAction.ESCALATE_HUMAN
