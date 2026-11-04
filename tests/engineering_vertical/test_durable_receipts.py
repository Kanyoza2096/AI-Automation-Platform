from copy import deepcopy
from types import SimpleNamespace

from services.kdo_engineering_receipts import DurableActionReceiptVerifier
from services.execution_ledger import _workspace_uuid


SCOPE = {
    "tenant_id": "tenant-a", "workspace_id": "workspace-a", "task_id": "task-a",
    "decision_id": "decision-1", "action_type": "run_tests",
}
RECEIPT = {"execution_id": "exe-1", "intent_id": "intent-1", "status": "succeeded"}
INTENT = {
    "workspace_id": "workspace-a", "intent_id": "intent-1", "action_type": "run_tests",
    "decision_id": "decision-1", "execution_id": "exe-1", "status": "succeeded",
    "parameters": {"tenant_id": "tenant-a", "workspace_id": "workspace-a", "task_id": "task-a"},
}
EXECUTION = {
    "workspace_id": _workspace_uuid("workspace-a"), "execution_id": "exe-1",
    "action": "action.run_tests", "status": "succeeded", "provider_object_id": "test-run-1",
    "evidence": {"intent_id": "intent-1", "decision_id": "decision-1", "source": "kdo_engineering_vertical"},
}


class Query:
    def __init__(self, rows):
        self.rows = rows
        self.filters = {}
    def select(self, _columns):
        return self
    def eq(self, key, value):
        self.filters[key] = value
        return self
    def execute(self):
        data = [row for row in self.rows if all(row.get(k) == v for k, v in self.filters.items())]
        return SimpleNamespace(data=data)


class DB:
    def __init__(self, intent_rows=None, execution_rows=None, error=False):
        self.tables = {"action_outbox": intent_rows if intent_rows is not None else [deepcopy(INTENT)],
                       "external_execution_ledger": execution_rows if execution_rows is not None else [deepcopy(EXECUTION)]}
        self.error = error
    def table(self, name):
        if self.error:
            raise RuntimeError("database unavailable")
        return Query(self.tables[name])


def test_durable_verifier_accepts_matching_authoritative_records():
    assert DurableActionReceiptVerifier(DB()).verify(SCOPE, RECEIPT) is True


def test_durable_verifier_rejects_missing_ledger_row():
    assert DurableActionReceiptVerifier(DB(execution_rows=[])).verify(SCOPE, RECEIPT) is False


def test_durable_verifier_rejects_scope_or_decision_mismatch():
    row = deepcopy(INTENT)
    row["decision_id"] = "forged"
    assert DurableActionReceiptVerifier(DB(intent_rows=[row])).verify(SCOPE, RECEIPT) is False
    row = deepcopy(INTENT)
    row["parameters"]["tenant_id"] = "other-tenant"
    assert DurableActionReceiptVerifier(DB(intent_rows=[row])).verify(SCOPE, RECEIPT) is False


def test_durable_verifier_rejects_nonterminal_or_missing_provider_receipt():
    row = deepcopy(EXECUTION)
    row["status"] = "uncertain"
    assert DurableActionReceiptVerifier(DB(execution_rows=[row])).verify(SCOPE, RECEIPT) is False
    row = deepcopy(EXECUTION)
    row["provider_object_id"] = None
    assert DurableActionReceiptVerifier(DB(execution_rows=[row])).verify(SCOPE, RECEIPT) is False


def test_durable_verifier_fails_closed_on_database_error_and_duplicate_rows():
    assert DurableActionReceiptVerifier(DB(error=True)).verify(SCOPE, RECEIPT) is False
    assert DurableActionReceiptVerifier(DB(intent_rows=[deepcopy(INTENT), deepcopy(INTENT)])).verify(SCOPE, RECEIPT) is False


def test_durable_verifier_binds_queue_receipt_to_approved_plan_and_idempotency_key():
    row = deepcopy(INTENT)
    row["parameters"]["plan_digest"] = "a" * 64
    row["parameters"]["job_idempotency_key"] = "kdo-build:tenant-a:workspace-a:task-a:" + "a" * 64
    scope = {**SCOPE, "plan_digest": "a" * 64,
             "job_idempotency_key": row["parameters"]["job_idempotency_key"]}
    verifier = DurableActionReceiptVerifier(DB(intent_rows=[row]))
    assert verifier.verify(scope, RECEIPT) is True

    wrong_digest = {**scope, "plan_digest": "b" * 64}
    assert verifier.verify(wrong_digest, RECEIPT) is False
    wrong_key = {**scope, "job_idempotency_key": "different-job-key"}
    assert verifier.verify(wrong_key, RECEIPT) is False
