import asyncio
from datetime import datetime, timezone

from services.action_kernel import build_default_action_kernel
from services.sector_action_implementation_gate import build_sector_action_implementation_gate
from plugins.industries.marketing_media.repository import MarketingMediaRepository


def test_marketing_schedule_content_is_canonical_internal_domain_gate():
    gate = build_sector_action_implementation_gate()
    row = next(x for x in gate["actions"] if x["sector_action"] == "marketing_schedule_content")
    assert row["gate"]["status"] == "READY_FOR_CANONICAL_EXECUTION_PATH"
    registry = __import__("services.sector_action_contract_registry", fromlist=["build_sector_action_contract_registry"]).build_sector_action_contract_registry()
    record = next(x for x in registry["contracts"] if x["sector_action"] == "marketing_schedule_content")
    assert record["execution_kind"] == "INTERNAL_DOMAIN"
    assert record["connector"]["binding"] == "NOT_REQUIRED_INTERNAL"
    assert record["receipt"]["protocol"] == "KDOInternalDomainReceipt"
    assert record["reconciliation"] == "NOT_REQUIRED_INTERNAL"


def test_marketing_schedule_content_executes_as_durable_domain_write():
    repo = MarketingMediaRepository(None)
    campaign = repo.create_campaign("mkt-a", {"name": "Q4", "objective_id": "obj-1", "budget": 100})
    kernel = build_default_action_kernel(allow_in_memory=True, marketing_repository=repo)
    result = asyncio.run(kernel.execute(
        workspace_id="mkt-a",
        action_type="marketing_schedule_content",
        parameters={
            "campaign_id": campaign.id,
            "candidate_id": "cand-1",
            "platform": "linkedin",
            "scheduled_at": datetime.now(timezone.utc).isoformat(),
            "decision_id": "dec-1",
        },
        risk_level="medium",
        decision_id="dec-1",
        source="test",
    ))
    assert result["status"] == "succeeded"
    assert result["reconciliation_required"] is False
    assert result["output"]["internal_receipt"]["kind"] == "KDOInternalDomainReceipt"
    assert result["output"]["object_id"].startswith("sch-")
