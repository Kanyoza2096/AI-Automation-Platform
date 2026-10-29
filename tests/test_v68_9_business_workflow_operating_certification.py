from services.business_automation import BusinessAutomationEngine
from services.v68_business_workflow_operating_certification import (
    WorkflowOperatingState,
    assert_workflow_certification_is_non_executing,
    certify_workflow_operating_contract,
)


def test_all_catalog_workflows_have_operating_contracts():
    results = certify_workflow_operating_contract()
    assert results
    assert [r for r in results if r.state == WorkflowOperatingState.BLOCKED] == []


def test_customer_facing_contract_has_trigger_action_outcome_and_requirements():
    results = certify_workflow_operating_contract()
    assert all(r.trigger and r.action for r in results)
    assert all("business_outcome_declared" in r.checks for r in results)
    assert all("runtime_requirements_declared" in r.checks for r in results)


def test_event_workflows_use_explicit_installation_contract():
    results = certify_workflow_operating_contract()
    event_results = [r for r in results if r.trigger not in {"schedule", "manual"}]
    assert event_results
    assert all("event_trigger_installation_contract" in r.checks for r in event_results)


def test_workflow_installation_is_disabled_by_default():
    results = certify_workflow_operating_contract()
    assert all(r.enabled_by_default is False for r in results)
    assert all("installation_disabled_by_default" in r.checks for r in results)


def test_high_impact_workflows_remain_review_required():
    results = certify_workflow_operating_contract()
    high = [r for r in results if r.risk in {"high", "critical"}]
    assert high
    assert all(r.state == WorkflowOperatingState.REVIEW_REQUIRED for r in high)


def test_certification_never_executes_runtime_handlers():
    engine = BusinessAutomationEngine()
    called = {"value": False}

    async def forbidden(*args, **kwargs):
        called["value"] = True
        return {"ok": True}

    engine._actions["notify_operator"] = forbidden
    certify_workflow_operating_contract(automation_engine=engine)
    assert called["value"] is False


def test_non_executing_boundary_rejects_install_now():
    assert_workflow_certification_is_non_executing({"authority": "none"})
    try:
        assert_workflow_certification_is_non_executing({"install_now": True})
    except ValueError:
        pass
    else:
        raise AssertionError("installation metadata must be rejected")
