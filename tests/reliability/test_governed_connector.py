import asyncio

from services.connectors.connector import ConnectorResult
from services.connectors.governed_connector import GovernedConnector
from services.execution_ledger import ExecutionLedger


class FakeConnector:
    platform = "fake"

    def __init__(self):
        self.calls = 0

    def publish(self, content, **kwargs):
        self.calls += 1
        return ConnectorResult(ok=True, platform="fake", post_id="post-1")

    def edit(self, post_id, content):
        self.calls += 1
        return ConnectorResult(ok=True, platform="fake", post_id=post_id)

    def delete(self, post_id):
        self.calls += 1
        return ConnectorResult(ok=True, platform="fake", post_id=post_id)

    def health(self):
        return None

    def validate(self):
        return True

    def fetch_comments(self, post_id, limit=50):
        return []

    def fetch_messages(self, limit=50):
        return []


def test_governed_publish_is_idempotent():
    connector = FakeConnector()
    governed = GovernedConnector(connector, workspace_id="ws", ledger=ExecutionLedger(allow_in_memory=True))

    first = asyncio.run(governed.publish("hello", idempotency_key="stable-key"))
    second = asyncio.run(governed.publish("hello", idempotency_key="stable-key"))

    assert first["status"] == "succeeded"
    assert second["replayed"] is True
    assert connector.calls == 1


def test_governed_transport_failure_is_uncertain():
    class Failing(FakeConnector):
        def publish(self, content, **kwargs):
            self.calls += 1
            raise TimeoutError("lost response")

    connector = Failing()
    governed = GovernedConnector(connector, workspace_id="ws", ledger=ExecutionLedger(allow_in_memory=True))
    result = asyncio.run(governed.publish("hello", idempotency_key="stable-timeout"))

    assert result["status"] == "uncertain"
    assert result["reconciliation_required"] is True


def test_receipt_persistence_failure_is_uncertain_not_retryable_failure():
    from services.external_action_executor import ExternalActionExecutor
    from services.execution_ledger import ExecutionLedger

    class BrokenLedger(ExecutionLedger):
        def complete(self, *args, **kwargs):
            raise RuntimeError("database receipt write failed")

    connector = FakeConnector()
    ledger = BrokenLedger(allow_in_memory=True)
    result = asyncio.run(ExternalActionExecutor(ledger).execute(
        workspace_id="ws", action="publish", idempotency_key="receipt-failure",
        provider="fake", operation=lambda: connector.publish("hello")
    ))
    assert result["status"] == "uncertain"
    assert result["reconciliation_required"] is True
    assert result["provider_object_id"] == "post-1"
    assert connector.calls == 1


def test_governed_deterministic_connector_rejection_is_failed_not_uncertain():
    class Rejected(FakeConnector):
        def publish(self, content, **kwargs):
            self.calls += 1
            return ConnectorResult(ok=False, platform="fake", error="invalid request", outcome="failed")

    connector = Rejected()
    governed = GovernedConnector(connector, workspace_id="ws", ledger=ExecutionLedger(allow_in_memory=True))
    result = asyncio.run(governed.publish("hello", idempotency_key="deterministic-failure"))
    assert result["status"] == "failed"
    assert result.get("reconciliation_required", False) is False


def test_governed_explicit_uncertain_connector_result_is_uncertain():
    class Ambiguous(FakeConnector):
        def publish(self, content, **kwargs):
            self.calls += 1
            return ConnectorResult(ok=False, platform="fake", error="provider timeout", outcome="uncertain")

    connector = Ambiguous()
    governed = GovernedConnector(connector, workspace_id="ws", ledger=ExecutionLedger(allow_in_memory=True))
    result = asyncio.run(governed.publish("hello", idempotency_key="explicit-uncertain"))
    assert result["status"] == "uncertain"
    assert result["reconciliation_required"] is True
