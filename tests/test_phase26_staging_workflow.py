"""P26 safe, local staging workflow: no external adapters or side effects."""
import asyncio

from services.director import Director
from services.director_bindings import bind_director_capabilities


def test_staging_workflow_inventory_bind_safe_diagnostic_and_verify_result():
    director = Director(supabase_client=None)
    binding_report = bind_director_capabilities(director, container=None)

    catalog = director.capability_catalog()
    assert catalog
    assert binding_report["self_diagnostic"] == "bound"
    assert catalog["self_diagnostic"]["runtime_binding_confirmed"] is True
    assert catalog["self_diagnostic"]["runtime_dependencies_verified"] is False

    result = asyncio.run(director.execute_capability("self_diagnostic", {}))
    assert result["ok"] is True
    assert result["capability"] == "self_diagnostic"
    handler_result = result["result"]
    assert handler_result["ok"] is True
    assert isinstance(handler_result.get("result"), dict)
    assert "capabilities" in handler_result["result"]


def test_staging_workflow_refuses_proposal_capability_on_direct_execution_path():
    director = Director(supabase_client=None)
    bind_director_capabilities(director, container=None)

    result = asyncio.run(director.execute_capability("scale_worker_pool", {
        "pool_name": "staging-only", "target_workers": 2,
    }))
    assert result["ok"] is False
    assert result["status"] == "governance_required"
    assert result["error"] == "proposal_governed_handoff_required"


def test_staging_workflow_never_equates_static_binding_with_live_dependency_health():
    director = Director(supabase_client=None)
    bind_director_capabilities(director, container=None)
    catalog = director.capability_catalog()

    assert catalog["self_diagnostic"]["binding_status"] == "bound"
    assert catalog["self_diagnostic"]["runtime_dependencies_verified"] is False
    assert all(not item["runtime_dependencies_verified"] for item in catalog.values())


def test_staging_workflow_unknown_outcome_requires_reconciliation_before_retry():
    from services.director.models import Authority, Risk, Sector, Status, Task

    director = Director(supabase_client=None)
    director.tasks["p26-unknown"] = Task(
        id="p26-unknown", sector=Sector.SELF, description="staging unknown outcome",
        capability="self_diagnostic", authority=Authority.SUGGEST, risk=Risk.LOW,
        status=Status.FLAGGED,
        result={"ok": True, "outcome_verification": {"outcome": "unknown"}},
    )
    recovery = director.assess_task_recovery("p26-unknown")
    assert recovery["automatic_retry_allowed"] is False
    assert recovery["retry_safety"] == "unsafe_until_reconciled"
    assert recovery["recovery_evidence"]["reconciliation_required"] is True
