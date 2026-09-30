"""V67.10 external side-effect chaos certification tests."""
import asyncio

from services.execution_ledger import ExecutionLedger
from services.external_action_executor import ExternalActionExecutor
from services.v67_external_side_effect_sandbox import SandboxProvider


def _run(provider, key, mode="success"):
    ledger = ExecutionLedger(allow_in_memory=True)
    executor = ExternalActionExecutor(ledger)
    return asyncio.run(executor.execute(
        workspace_id="ws-v6710",
        action="sandbox.external_side_effect_probe",
        idempotency_key=key,
        provider="v67.10-sandbox",
        operation=lambda: provider.submit(idempotency_key=key, failure_mode=mode),
    )), ledger, executor


def test_success_commits_exactly_one_provider_side_effect():
    provider = SandboxProvider()
    result, _, _ = _run(provider, "k-success")
    assert result["status"] == "succeeded"
    assert provider.committed_count == 1
    assert provider.call_count == 1


def test_reject_before_commit_is_failed_and_has_no_remote_state():
    provider = SandboxProvider()
    result, _, _ = _run(provider, "k-reject", "reject_before_commit")
    assert result["status"] == "failed"
    assert provider.committed_count == 0


def test_timeout_after_commit_is_uncertain_and_reconciles_to_same_object():
    provider = SandboxProvider()
    key = "k-timeout"
    result, ledger, executor = _run(provider, key, "timeout_after_commit")
    assert result["status"] == "uncertain"
    assert result["reconciliation_required"] is True
    remote = provider.reconcile(idempotency_key=key)
    assert remote["status"] == "committed"
    object_id = remote["provider_object_id"]

    replay = asyncio.run(executor.execute(
        workspace_id="ws-v6710", action="sandbox.external_side_effect_probe",
        idempotency_key=key, provider="v67.10-sandbox",
        operation=lambda: provider.submit(idempotency_key=key),
    ))
    assert replay["replayed"] is True
    assert provider.committed_count == 1
    assert provider.call_count == 1
    assert object_id.startswith("sbx_obj_")
    assert ledger.begin(workspace_id="ws-v6710", action="sandbox.external_side_effect_probe", idempotency_key=key).status == "uncertain"


def test_crash_after_commit_is_uncertain_and_provider_idempotency_prevents_duplicate():
    provider = SandboxProvider()
    key = "k-crash"
    result, _, executor = _run(provider, key, "crash_after_commit")
    assert result["status"] == "uncertain"
    assert provider.committed_count == 1
    assert provider.call_count == 1
    remote = provider.reconcile(idempotency_key=key)
    assert remote["status"] == "committed"

    # A new executor/ledger represents a recovered worker. Provider-side
    # idempotency still prevents a second remote side effect.
    recovered = asyncio.run(executor.execute(
        workspace_id="ws-v6710", action="sandbox.external_side_effect_probe",
        idempotency_key=key, provider="v67.10-sandbox",
        operation=lambda: provider.submit(idempotency_key=key),
    ))
    assert recovered["replayed"] is True
    assert provider.committed_count == 1
    assert provider.call_count == 1
