import asyncio
from datetime import datetime, timezone

from services.v71_17_durable_workforce_execution import (
    DurableWorkforceExecutionCoordinator, _envelope_from_manifest,
    WorkforceExecutionBlock,
)


def manifest():
    from services.business_autonomy_envelope import BusinessAutonomyEnvelope, SuccessMetric, AutonomyMode, EnvelopeState
    env = BusinessAutonomyEnvelope(
        workspace_id="ws", objective="test", success_metrics=(SuccessMetric("x", ">=", 1),),
        allowed_actions=("notify_operator",), scope={"workflow_ids": ["management.plan-risk-escalation"]},
        mode=AutonomyMode.EXECUTE, state=EnvelopeState.ACTIVE,
    )
    d = env.to_dict()
    return d


def test_manifest_round_trip_preserves_fingerprint():
    d = manifest()
    rebuilt = _envelope_from_manifest(d, "ws")
    assert rebuilt.fingerprint() == d["fingerprint"]
    assert rebuilt.workspace_id == "ws"


def test_missing_manifest_is_blocked_by_contract():
    class R:
        pass
    run = R(); run.event = {}; run.workflow_id = "management.plan-risk-escalation"
    try:
        DurableWorkforceExecutionCoordinator._manifest(run)
    except ValueError as exc:
        assert str(exc) == WorkforceExecutionBlock.GOVERNANCE_MANIFEST_REQUIRED.value
    else:
        raise AssertionError("manifest should be required")


def test_status_has_no_parallel_executor():
    class PR:
        pass
    c = DurableWorkforceExecutionCoordinator(production_runtime=PR(), workspace_id="ws")
    s = c.status()
    assert s["parallel_execution_plane"] is False
    assert s["provider_execution_here"] is False
