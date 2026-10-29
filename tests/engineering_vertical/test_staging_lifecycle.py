"""Staging readiness and end-to-end governance regression tests.

These tests use deterministic fixtures; they do not claim to exercise live
staging credentials, production RLS, or a deployed Council service.
"""
from types import SimpleNamespace

import pytest

from services.action_kernel import ActionContract, ActionKernel
from services.kdo_engineering_staging import preflight_staging_receipt_schema, validate_staging_scope
from services.kdo_engineering_governed_actions import (
    GovernedEngineeringActionError, execute_council_governed_engineering_action,
)

TASK = {"tenant_id": "tenant-staging", "workspace_id": "workspace-staging", "task_id": "task-staging"}


class SchemaQuery:
    def __init__(self, db, table):
        self.db, self.table_name = db, table
        self.columns = ""
    def select(self, columns):
        self.columns = columns
        return self
    def limit(self, count):
        if count != 0:
            raise AssertionError("preflight must use zero-row queries")
        return self
    def execute(self):
        if self.table_name in self.db.fail_tables:
            raise PermissionError("details intentionally hidden")
        return SimpleNamespace(data=[])


class SchemaDB:
    def __init__(self, fail_tables=()):
        self.fail_tables = set(fail_tables)
    def table(self, name):
        if name not in {"action_outbox", "external_execution_ledger"}:
            raise KeyError(name)
        return SchemaQuery(self, name)


def decision(approved=True, decision_id="stage-decision-1"):
    return {"is_approved": approved, "decision_id": decision_id}


def test_staging_preflight_is_read_only_and_checks_both_receipt_tables():
    result = preflight_staging_receipt_schema(SchemaDB())
    assert result["ready"] is True
    assert result["read_only"] is True
    assert result["checks"]["action_outbox"]["columns_checked"] == 7
    assert result["checks"]["external_execution_ledger"]["columns_checked"] == 6
    assert "live_council_and_action_kernel_lifecycle_not_executed" in result["limitations"]


def test_staging_preflight_fails_closed_when_one_table_is_unavailable():
    result = preflight_staging_receipt_schema(SchemaDB({"external_execution_ledger"}))
    assert result["ready"] is False
    assert result["blockers"] == ["external_execution_ledger_receipt_schema_or_read_access_failed"]


def test_staging_preflight_requires_a_database_client():
    result = preflight_staging_receipt_schema(None)
    assert result["ready"] is False
    assert result["blockers"] == ["authoritative_database_client_required"]


def test_scope_validator_requires_all_authoritative_identifiers():
    ok, blockers = validate_staging_scope({"tenant_id": "t", "workspace_id": "w"})
    assert ok is False
    assert "task_id_required" in blockers
    assert "decision_id_required" in blockers


@pytest.mark.asyncio
async def test_full_governed_lifecycle_executes_once_and_verifies_receipt():
    kernel = ActionKernel(allow_in_memory=True)
    calls = []
    async def handler(parameters, intent):
        calls.append(intent.idempotency_key)
        return {"status": "succeeded", "provider_object_id": "staging-check-1",
                "output": {"check": "ok"}}
    kernel.register(ActionContract(action_type="engineering_staging_probe", risk_level="low", handler=handler))
    async def approve(**kwargs):
        assert kwargs["metadata"]["tenant_id"] == TASK["tenant_id"]
        return decision()
    def verify(scope, receipt):
        return (scope["tenant_id"] == TASK["tenant_id"]
                and scope["workspace_id"] == TASK["workspace_id"]
                and scope["decision_id"] == "stage-decision-1"
                and bool(receipt["execution_id"]) and bool(receipt["intent_id"]))

    result = await execute_council_governed_engineering_action(
        task=TASK, action_type="engineering_staging_probe", parameters={"probe": True},
        agent_id="staging-agent", tool_name="staging-probe", objective="Validate staging integration",
        action_kernel=kernel, council_deliberate=approve, receipt_verifier=verify,
        idempotency_key="staging-probe-fixed-key",
    )
    assert result["status"] == "succeeded", result
    assert result["receipt_verified"] is True
    replay = await execute_council_governed_engineering_action(
        task=TASK, action_type="engineering_staging_probe", parameters={"probe": True},
        agent_id="staging-agent", tool_name="staging-probe", objective="Retry the same staging request",
        action_kernel=kernel, council_deliberate=approve, receipt_verifier=verify,
        idempotency_key="staging-probe-fixed-key",
    )
    assert replay["status"] == "succeeded"
    assert replay["receipt"]["execution_id"] == result["receipt"]["execution_id"]
    assert len(calls) == 1  # Same idempotency key does not repeat the side effect.


@pytest.mark.asyncio
async def test_denial_does_not_invoke_action_handler():
    kernel = ActionKernel(allow_in_memory=True)
    calls = []
    async def handler(parameters, intent):
        calls.append(intent.intent_id if hasattr(intent, "intent_id") else "called")
        return {"status": "succeeded", "provider_object_id": "should-not-exist"}
    kernel.register(ActionContract(action_type="engineering_staging_probe", handler=handler))
    with pytest.raises(GovernedEngineeringActionError, match="approval_not_confirmed"):
        await execute_council_governed_engineering_action(
            task=TASK, action_type="engineering_staging_probe", parameters={},
            agent_id="staging-agent", tool_name="staging-probe", objective="Denial test",
            action_kernel=kernel, council_deliberate=lambda **kwargs: decision(False),
            receipt_verifier=lambda scope, receipt: True,
        )
    assert calls == []


@pytest.mark.asyncio
async def test_uncertain_handler_outcome_never_becomes_verified_success():
    kernel = ActionKernel(allow_in_memory=True)
    async def handler(parameters, intent):
        return {"status": "uncertain", "provider_object_id": None,
                "reconciliation_required": True, "error": "simulated interrupted provider call"}
    kernel.register(ActionContract(action_type="engineering_staging_probe", handler=handler))
    result = await execute_council_governed_engineering_action(
        task=TASK, action_type="engineering_staging_probe", parameters={},
        agent_id="staging-agent", tool_name="staging-probe", objective="Recovery test",
        action_kernel=kernel, council_deliberate=lambda **kwargs: decision(),
        receipt_verifier=lambda scope, receipt: True,
    )
    assert result["status"] == "uncertain"
    assert result["reconciliation_required"] is True
