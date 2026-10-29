import pytest

from services.action_kernel import build_default_action_kernel
from services.business_automation import BusinessAutomationEngine
from services.business_automation_catalog import get_template
from services.v68_business_capability_certification import CertificationState, certify_catalog
from services.sales_orchestration import SalesOrchestrator


def test_capability_certification_has_no_structural_blockers():
    results = certify_catalog()
    blocked = [r for r in results if r.state == CertificationState.BLOCKED]
    assert blocked == []


def test_notify_operator_is_a_real_kernel_contract():
    kernel = build_default_action_kernel(allow_in_memory=True)
    contract = kernel._contracts["notify_operator"]
    assert contract.risk_level == "medium"
    assert contract.required_parameters == ("message",)
    assert contract.handler is not None


def test_report_is_explicitly_read_only():
    engine = BusinessAutomationEngine()
    assert "generate_workspace_report" in engine.READ_ONLY_ACTIONS
    result = next(r for r in certify_catalog(automation_engine=engine) if r.action == "generate_workspace_report")
    assert result.state == CertificationState.CERTIFIED
    assert "read_only_capability_classified" in result.checks
    assert "action_kernel_contract_missing" not in result.reasons


def test_sales_orchestrator_is_explicit_kernel_delegator():
    assert SalesOrchestrator.EXECUTION_PLANE == "action_kernel_delegate"
    result = next(r for r in certify_catalog() if r.action == "sales_orchestrate")
    assert result.state == CertificationState.CERTIFIED
    assert "orchestrator_delegates_to_action_kernel" in result.checks


def test_high_impact_workflows_remain_review_required():
    results = certify_catalog()
    high = [r for r in results if r.risk in {"high", "critical"}]
    assert high
    assert all(r.state == CertificationState.REVIEW_REQUIRED for r in high)
    assert all(r.requires_approval for r in high)
