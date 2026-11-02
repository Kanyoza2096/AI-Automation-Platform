"""Regression tests for truthful reliability remediation planning."""

from services.reliability.models import RemediationAction
from services.reliability.planner import CAUSE_PLAYBOOK


def test_generic_service_degradation_does_not_schedule_unimplemented_refresh():
    assert CAUSE_PLAYBOOK["service_health_degraded"] is RemediationAction.ESCALATE_HUMAN
    assert CAUSE_PLAYBOOK["degraded_engines"] is RemediationAction.ESCALATE_HUMAN


def test_prior_memory_cannot_restore_unimplemented_refresh_action():
    from services.reliability.memory import IncidentMemory
    from services.reliability.models import Diagnosis, Incident
    from services.reliability.planner import RepairPlanner
    from services.reliability.policies import AutonomyPolicy

    memory = IncidentMemory()
    memory.record(
        incident_id="old-incident",
        cause="service_health_degraded",
        action=RemediationAction.REFRESH_CONNECTION.value,
        outcome="success",
    )
    planner = RepairPlanner(AutonomyPolicy(), memory=memory)
    plan = planner.plan(
        Incident(component="job_service"),
        Diagnosis(
            incident_id="new-incident",
            probable_cause="service_health_degraded",
            confidence=0.7,
            related_components=["job_service"],
        ),
    )

    assert plan.action is RemediationAction.ESCALATE_HUMAN
    assert plan.requires_human is True
