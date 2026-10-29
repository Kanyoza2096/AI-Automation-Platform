from types import SimpleNamespace

import pytest

from services.action_kernel import ActionContract, ActionKernel
from services.kdo_engineering_governed_actions import (
    GovernedEngineeringActionError,
    execute_council_governed_engineering_action,
)
from services.kdo_engineering_team_events import TeamEventStore

TASK = {"tenant_id": "tenant-a", "workspace_id": "workspace-a", "task_id": "task-a"}


class FakeKernel:
    def __init__(self, result=None, contract=True):
        self.calls = []
        self.result = result or {"status": "succeeded", "execution_id": "exec-1", "intent_id": "intent-1", "action_type": "run_tests"}
        self.contract = contract

    def get_contract_metadata(self, action_type):
        return {"action_type": action_type, "risk_level": "low"} if self.contract else None

    async def execute(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


def council_decision(*, approved=True, decision_id="council-decision-1"):
    return SimpleNamespace(is_approved=approved, is_blocked=not approved,
                           needs_escalation=False, decision_id=decision_id)


@pytest.mark.asyncio
async def test_denied_council_never_calls_action_kernel():
    kernel = FakeKernel()
    async def deny(**kwargs):
        assert kwargs["action_name"] == "run_tests"
        return council_decision(approved=False)
    with pytest.raises(GovernedEngineeringActionError, match="approval_not_confirmed"):
        await execute_council_governed_engineering_action(
            task=TASK, action_type="run_tests", parameters={}, agent_id="test-agent",
            tool_name="pytest", objective="Run bounded test suite", action_kernel=kernel,
            council_deliberate=deny,
        )
    assert kernel.calls == []


@pytest.mark.asyncio
async def test_council_exception_fails_closed():
    kernel = FakeKernel()
    async def fail(**kwargs):
        raise TimeoutError("council unavailable")
    with pytest.raises(GovernedEngineeringActionError, match="deliberation_failed_closed"):
        await execute_council_governed_engineering_action(
            task=TASK, action_type="run_tests", parameters={}, agent_id="test-agent",
            tool_name="pytest", objective="Run tests", action_kernel=kernel, council_deliberate=fail,
        )
    assert kernel.calls == []


@pytest.mark.asyncio
async def test_caller_supplied_decision_id_is_not_used_as_authority():
    kernel = FakeKernel()
    async def deny(**kwargs):
        return {"is_approved": True}  # missing Council-issued decision id
    with pytest.raises(GovernedEngineeringActionError, match="decision_id_missing"):
        await execute_council_governed_engineering_action(
            task=TASK, action_type="run_tests", parameters={"decision_id": "forged"},
            agent_id="test-agent", tool_name="pytest", objective="Run tests",
            action_kernel=kernel, council_deliberate=deny,
        )
    assert kernel.calls == []


@pytest.mark.asyncio
async def test_unknown_action_contract_fails_before_council_or_execution():
    kernel = FakeKernel(contract=False)
    async def should_not_call(**kwargs):
        raise AssertionError("Council must not be called for an unregistered action")
    with pytest.raises(GovernedEngineeringActionError, match="registered_action_contract_required"):
        await execute_council_governed_engineering_action(
            task=TASK, action_type="unknown_action", parameters={}, agent_id="a", tool_name="tool",
            objective="test", action_kernel=kernel, council_deliberate=should_not_call,
        )
    assert kernel.calls == []


@pytest.mark.asyncio
async def test_approved_action_uses_council_decision_and_exact_scope():
    kernel = FakeKernel()
    seen = {}
    async def approve(**kwargs):
        seen.update(kwargs)
        return council_decision()
    result = await execute_council_governed_engineering_action(
        task=TASK, action_type="run_tests", parameters={"command": ["pytest"]},
        agent_id="test-agent", tool_name="pytest", objective="Run bounded tests",
        action_kernel=kernel, council_deliberate=approve, receipt_verifier=lambda scope, receipt: True,
    )
    assert result["status"] == "succeeded"
    assert result["receipt"]["execution_id"] == "exec-1"
    assert seen["action_params"]["task_id"] == TASK["task_id"]
    assert seen["metadata"]["tenant_id"] == TASK["tenant_id"]
    call = kernel.calls[0]
    assert call["decision_id"] == "council-decision-1"
    assert call["workspace_id"] == TASK["workspace_id"]
    assert call["parameters"]["tenant_id"] == TASK["tenant_id"]
    assert "decision_id" not in call["parameters"]  # Kernel receives it through its typed argument.


@pytest.mark.asyncio
async def test_success_without_authoritative_receipt_is_uncertain():
    kernel = FakeKernel(result={"status": "succeeded", "execution_id": "", "intent_id": ""})
    result = await execute_council_governed_engineering_action(
        task=TASK, action_type="run_tests", parameters={}, agent_id="test-agent",
        tool_name="pytest", objective="Run tests", action_kernel=kernel,
        council_deliberate=lambda **kwargs: council_decision(),
    )
    assert result["status"] == "uncertain"
    assert result["reconciliation_required"] is True


@pytest.mark.asyncio
async def test_event_store_records_proposal_approval_and_receipt():
    store = TeamEventStore()
    result = await execute_council_governed_engineering_action(
        task=TASK, action_type="run_tests", parameters={}, agent_id="test-agent",
        tool_name="pytest", objective="Run tests", action_kernel=FakeKernel(),
        event_store=store, council_deliberate=lambda **kwargs: council_decision(),
        receipt_verifier=lambda scope, receipt: True,
    )
    events = store.list_events(task_id="task-a", tenant_id="tenant-a", workspace_id="workspace-a")
    assert [event["event_type"] for event in events] == [
        "governed_action_proposed", "governed_action_approved", "governed_action_receipt_verified"
    ]
    assert store.verify_chain(task_id="task-a", tenant_id="tenant-a", workspace_id="workspace-a")
    assert result["status"] == "succeeded"


@pytest.mark.asyncio
async def test_actual_action_kernel_contract_is_invoked_after_council_approval():
    called = []
    kernel = ActionKernel(allow_in_memory=True)
    async def handler(parameters, intent):
        called.append((parameters["task_id"], intent.decision_id))
        return {"status": "succeeded", "provider_object_id": "test-run-receipt-1", "output": {"test_exit_code": 0}}
    kernel.register(ActionContract(action_type="run_tests", risk_level="low", handler=handler))
    result = await execute_council_governed_engineering_action(
        task=TASK, action_type="run_tests", parameters={"command": ["pytest"]},
        agent_id="test-agent", tool_name="pytest", objective="Run bounded tests",
        action_kernel=kernel, council_deliberate=lambda **kwargs: council_decision(),
        receipt_verifier=lambda scope, receipt: True,
    )
    assert called == [("task-a", "council-decision-1")]
    assert result["status"] == "succeeded"
    assert result["receipt"]["execution_id"]
    assert result["receipt"]["intent_id"]
    assert result["receipt_verified"] is True


@pytest.mark.asyncio
async def test_kernel_response_without_canonical_receipt_verifier_is_not_success():
    result = await execute_council_governed_engineering_action(
        task=TASK, action_type="run_tests", parameters={}, agent_id="test-agent",
        tool_name="pytest", objective="Run tests", action_kernel=FakeKernel(),
        council_deliberate=lambda **kwargs: council_decision(),
    )
    assert result["status"] == "uncertain"
    assert result["reason"] == "authoritative_receipt_verifier_not_configured"
    assert result["reconciliation_required"] is True


def test_missing_task_scope_fails_before_any_boundary_is_called():
    kernel = FakeKernel()
    with pytest.raises(GovernedEngineeringActionError, match="complete_task_scope_required"):
        import asyncio
        asyncio.run(execute_council_governed_engineering_action(
            task={"workspace_id": "workspace-a", "task_id": "task-a"}, action_type="run_tests",
            parameters={}, agent_id="a", tool_name="pytest", objective="test", action_kernel=kernel,
        ))
    assert kernel.calls == []


@pytest.mark.asyncio
async def test_default_configured_council_integration_is_used(monkeypatch):
    import services.kdo_engineering_governed_actions as bridge
    calls = []
    async def configured_council(**kwargs):
        calls.append(kwargs)
        return council_decision()
    monkeypatch.setattr(bridge, "_deliberate_action", configured_council)
    result = await bridge.execute_council_governed_engineering_action(
        task=TASK, action_type="run_tests", parameters={}, agent_id="test-agent",
        tool_name="pytest", objective="Run tests", action_kernel=FakeKernel(),
        receipt_verifier=lambda scope, receipt: scope["task_id"] == "task-a" and receipt["execution_id"] == "exec-1",
    )
    assert result["status"] == "succeeded"
    assert calls[0]["source_engine"] == "kdo_engineering_vertical"
    assert calls[0]["action_name"] == "run_tests"

@pytest.mark.asyncio
async def test_configured_kernel_database_auto_wires_authoritative_receipt_verifier():
    from tests.engineering_vertical.test_durable_receipts import DB
    kernel = FakeKernel(result={"status": "succeeded", "execution_id": "exe-1", "intent_id": "intent-1", "action_type": "run_tests"})
    kernel.db = DB()
    result = await execute_council_governed_engineering_action(
        task=TASK, action_type="run_tests", parameters={}, agent_id="test-agent",
        tool_name="pytest", objective="Verify durable receipt read-back", action_kernel=kernel,
        council_deliberate=lambda **kwargs: council_decision(decision_id="decision-1"),
    )
    assert result["status"] == "succeeded"
    assert result["receipt_verified"] is True
