from types import SimpleNamespace

import pytest

from services.action_kernel import ActionKernel
from services.sector_brand_schedule_execution import (
    BRAND_UPDATE_POSTING_SCHEDULE,
    register_brand_schedule_action,
)


class FakeBrandRepository:
    def __init__(self, brand):
        self.brand = brand
        self.saved = []

    def find_by_id(self, brand_id):
        return self.brand if self.brand and self.brand.id == brand_id else None

    def save(self, brand):
        self.saved.append(brand)
        return brand


@pytest.mark.asyncio
async def test_brand_schedule_change_executes_through_kernel_with_domain_receipt():
    brand = SimpleNamespace(
        id="brand-1", workspace_id="ws-1",
        posting_schedule={"content_hours": [8, 18]},
    )
    repo = FakeBrandRepository(brand)
    kernel = ActionKernel(None, allow_in_memory=True)
    register_brand_schedule_action(kernel, brand_repository=repo)

    result = await kernel.execute(
        workspace_id="ws-1",
        action_type=BRAND_UPDATE_POSTING_SCHEDULE,
        parameters={"brand_id": "brand-1", "posting_hours": [9, 17]},
        risk_level="medium",
        decision_id="approved-proposal-1",
        idempotency_key="proposal-1-schedule-v1",
        source="director_proposal",
    )

    assert result["status"] == "succeeded"
    assert result["execution_id"]
    assert result["intent_id"]
    assert len(repo.saved) == 1
    assert brand.posting_schedule["content_hours"] == [9, 17]
    assert result["output"]["internal_receipt"]["object_id"] == "brand-1"


@pytest.mark.asyncio
async def test_brand_schedule_action_rejects_cross_workspace_brand():
    brand = SimpleNamespace(
        id="brand-1", workspace_id="other-workspace",
        posting_schedule={"content_hours": [8]},
    )
    repo = FakeBrandRepository(brand)
    kernel = ActionKernel(None, allow_in_memory=True)
    register_brand_schedule_action(kernel, brand_repository=repo)

    result = await kernel.execute(
        workspace_id="ws-1",
        action_type=BRAND_UPDATE_POSTING_SCHEDULE,
        parameters={"brand_id": "brand-1", "posting_hours": [9]},
        risk_level="medium",
        decision_id="approved-proposal-1",
        idempotency_key="proposal-1-schedule-v1",
        source="director_proposal",
    )

    assert result["status"] == "failed"
    assert repo.saved == []
    assert brand.posting_schedule["content_hours"] == [8]


def test_brand_schedule_action_contract_is_approval_gated():
    kernel = ActionKernel(None, allow_in_memory=True)
    register_brand_schedule_action(kernel, brand_repository=FakeBrandRepository(None))
    contract = kernel.get_contract_metadata(BRAND_UPDATE_POSTING_SCHEDULE)
    assert contract["requires_approval"] is True
    assert contract["inline_with_decision"] is True
    assert contract["risk_level"] == "medium"


def test_director_binding_exposes_schedule_governed_handoff_only_with_durable_db():
    from services.director import Director
    from services.director_bindings import bind_director_capabilities

    director = Director(supabase_client=None)
    no_db = SimpleNamespace(supabase_service=None)
    status = bind_director_capabilities(director, no_db)
    assert status["update_brand_posting_schedule"] == "bound_governed_handoff_missing"
    assert director.capabilities["update_brand_posting_schedule"].governed_handoff is None

    director2 = Director(supabase_client=None)
    with_db = SimpleNamespace(supabase_service=SimpleNamespace(client=object()))
    status2 = bind_director_capabilities(director2, with_db)
    assert status2["update_brand_posting_schedule"] == "bound_governed_handoff"
    assert callable(director2.capabilities["update_brand_posting_schedule"].governed_handoff)
