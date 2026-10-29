from services.business_automation import BusinessAutomationEngine
from services.action_kernel import build_default_action_kernel
from services.v68_business_capability_certification import (
    CertificationState,
    assert_certification_is_non_executing,
    certify_catalog,
)


def test_catalog_is_wired_to_both_runtime_planes():
    results = certify_catalog(
        automation_engine=BusinessAutomationEngine(),
        action_kernel=build_default_action_kernel(allow_in_memory=True),
    )
    assert results
    blocked = {r.action for r in results if r.state == CertificationState.BLOCKED}
    assert blocked == set()
    assert any(r.state == CertificationState.REVIEW_REQUIRED for r in results)


def test_high_impact_workflows_require_review():
    results = certify_catalog()
    high = [r for r in results if r.risk in {"high", "critical"}]
    assert high
    assert all(r.state == CertificationState.REVIEW_REQUIRED for r in high)
    assert all(r.requires_approval for r in high)


def test_certification_never_executes_handlers():
    called = {"value": False}
    engine = BusinessAutomationEngine()
    original = engine._actions["crm_add_contact"]

    async def forbidden(*args, **kwargs):
        called["value"] = True
        return {"ok": True}

    engine._actions["crm_add_contact"] = forbidden
    certify_catalog(automation_engine=engine, action_kernel=build_default_action_kernel(allow_in_memory=True))
    assert called["value"] is False
    engine._actions["crm_add_contact"] = original


def test_non_executing_boundary_rejects_bypass_metadata():
    assert_certification_is_non_executing({"authority": "none", "execution_effect": "none"})
    try:
        assert_certification_is_non_executing({"auto_approve": True})
    except ValueError:
        pass
    else:
        raise AssertionError("bypass metadata must be rejected")
