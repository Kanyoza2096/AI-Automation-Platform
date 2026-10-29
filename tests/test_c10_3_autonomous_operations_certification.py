from pathlib import Path

from services.c10_3_autonomous_operations_certification import (
    C103Status, certify_c10_3, certify_repository_convergence,
)


ROOT = Path(__file__).resolve().parents[1]


def test_c10_3_repository_converges_autonomous_execution_on_governed_plane():
    checks = certify_repository_convergence(ROOT)
    assert checks
    assert all(c.status.value == "pass" for c in checks), checks


def test_c10_3_requires_external_distributed_evidence():
    result = certify_c10_3(root=ROOT)
    assert result.status == C103Status.REVIEW
    assert result.passed is False
    assert any(c.name == "distributed_autonomy_evidence" and c.status.value == "not_proven" for c in result.checks)


def test_verified_workforce_outcome_is_required_for_learning():
    import asyncio
    from core.autonomous_loop import AutonomousLoop

    loop = AutonomousLoop()
    calls = []
    async def capture(payload):
        calls.append(payload)
    loop._handle_outcome_recorded = capture

    asyncio.run(loop._handle_workforce_execution_result({
        "verified_outcome": False, "decision_id": "d1",
    }))
    assert calls == []

    asyncio.run(loop._handle_workforce_execution_result({
        "verified_outcome": True,
        "decision_id": "d1",
        "outcome_evidence": {"outcome": "success", "score": 0.9},
    }))
    assert calls[0]["decision_id"] == "d1"
    assert calls[0]["outcome"] == "success"
