import asyncio

from services.director import Director
from services.director_bindings import bind_director_capabilities, _build_handlers_for


def test_binding_report_flows_into_readiness_catalog_without_claiming_dependency_health():
    director = Director(supabase_client=None)
    report = bind_director_capabilities(director, container=None)

    catalog = director.capability_catalog()
    assert catalog["self_diagnostic"]["binding_status"] == report["self_diagnostic"] == "bound"
    assert catalog["self_diagnostic"]["runtime_binding_confirmed"] is True
    assert catalog["self_diagnostic"]["runtime_dependencies_verified"] is False
    assert catalog["self_diagnostic"]["readiness_scope"] == "static_wiring_and_contracts_only"
    assert catalog["update_brand_posting_schedule"]["binding_status"] == "bound_governed_handoff_missing"
    assert catalog["update_brand_posting_schedule"]["runtime_binding_confirmed"] is False
    assert director.capability_binding_at


def test_scale_worker_pool_does_not_report_success_for_intent_only(monkeypatch):
    director = Director(supabase_client=None)
    handlers = _build_handlers_for(director, container=None)

    result = asyncio.run(handlers["scale_worker_pool"]({
        "pool_name": "default",
        "target_workers": 3,
        "workspace_id": "ws-test",
    }))

    assert result["ok"] is False
    assert result["status"] == "unavailable"
    assert result["reason"] == "worker_scaling_adapter_not_bound"
    assert result["status_detail"] == "intent_not_executed"


def test_capability_catalog_keeps_unrun_binding_state_explicit():
    director = Director(supabase_client=None)
    entry = director.capability_catalog()["self_diagnostic"]

    assert entry["binding_status"] == "not_run"
    assert entry["runtime_binding_confirmed"] is False
    assert entry["runtime_dependencies_verified"] is False
