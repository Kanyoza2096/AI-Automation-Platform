from services.action_kernel import ActionIntent, build_default_action_kernel
from plugins.industries.cybersecurity.repository import CybersecurityRepository
from services.sector_action_contract_registry import build_sector_action_contract_registry
from services.sector_action_implementation_gate import build_sector_action_implementation_gate


def _by_action(rows):
    return {x["sector_action"]: x for x in rows}


def test_security_internal_records_are_canonical_internal_domain_actions():
    registry = _by_action(build_sector_action_contract_registry()["contracts"])
    gate = _by_action(build_sector_action_implementation_gate()["actions"])
    for action in ("security.record_response_outcome", "security.record_outcome"):
        assert registry[action]["execution_kind"] == "INTERNAL_DOMAIN"
        assert registry[action]["connector"]["binding"] == "NOT_REQUIRED_INTERNAL"
        assert registry[action]["receipt"]["protocol"] == "KDOInternalDomainReceipt"
        assert registry[action]["reconciliation"] == "NOT_REQUIRED_INTERNAL"
        assert gate[action]["gate"]["status"] == "READY_FOR_CANONICAL_EXECUTION_PATH"


import pytest

@pytest.mark.asyncio
async def test_security_internal_records_execute_through_action_kernel_and_persist():
    repo = CybersecurityRepository(None)
    kernel = build_default_action_kernel(allow_in_memory=True, cybersecurity_repository=repo)

    response = await kernel.execute(
        workspace_id="ws-sec",
        action_type="security_record_response_outcome",
        parameters={
            "action_type": "security_contain_asset",
            "target_id": "asset-1",
            "status": "verified",
            "evidence": {"source": "test"},
        },
        risk_level="medium",
        decision_id="dec-sec-1",
        idempotency_key="sec-response-1",
        source="test",
    )
    assert response["status"] == "succeeded"
    assert response["output"]["internal_receipt"]["kind"] == "KDOInternalDomainReceipt"
    assert response["reconciliation_required"] is False
    assert response["output"]["object_id"].startswith("sro-")

    outcome = await kernel.execute(
        workspace_id="ws-sec",
        action_type="security_record_outcome",
        parameters={
            "resource_type": "asset",
            "resource_id": "asset-1",
            "metric": "risk_score",
            "value": 12,
            "source": "test",
            "evidence": {"source": "test"},
        },
        risk_level="low",
        decision_id="dec-sec-2",
        idempotency_key="sec-outcome-1",
        source="test",
    )
    assert outcome["status"] == "succeeded"
    assert outcome["output"]["internal_receipt"]["kind"] == "KDOInternalDomainReceipt"
    assert outcome["reconciliation_required"] is False
    assert outcome["output"]["object_id"].startswith("sout-")
