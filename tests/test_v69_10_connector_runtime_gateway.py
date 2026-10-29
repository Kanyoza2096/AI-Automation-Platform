import asyncio
from datetime import datetime, timezone

import pytest

from services.business_integration_platform import AutonomyMode, ConnectionState, ConnectorContract, IntegrationConnection
from services.connectors.governed_connector import GovernedConnector
from services.execution_ledger import ExecutionLedger
from services.v69_connector_activation_registry import RegistryState, register_certification, transition_registry
from services.v69_connector_runtime_gateway import ConnectorRuntimeBlocked, RuntimeBlock, RuntimeConnectorGateway
from services.v69_reference_connector_certification import certify_facebook_reference

NOW = datetime(2026, 9, 14, tzinfo=timezone.utc)


def contract():
    return ConnectorContract(
        "facebook", "facebook", "1.0.0",
        capabilities=("publish", "lookup", "edit", "delete", "comments"),
        inbound_events=("comment", "message"),
        outbound_actions=("publish", "edit", "delete"),
        authentication_method="oauth2",
        risk_by_action={"publish": "medium", "edit": "medium", "delete": "high"},
        requires_reconciliation=True,
        tenant_scoped=True,
        certified=True,
    )


class FakeConnector:
    platform = "facebook"
    def __init__(self): self.calls = []
    def publish(self, content, **kwargs): self.calls.append(("publish", content)); return {"ok": True, "post_id": "p1"}
    def edit(self, post_id, content): self.calls.append(("edit", post_id, content)); return {"ok": True, "post_id": post_id}
    def delete(self, post_id): self.calls.append(("delete", post_id)); return {"ok": True, "post_id": post_id}
    def lookup_post(self, post_id): return None
    def health(self): return {"ok": True}
    def validate(self): return True
    def fetch_comments(self, post_id, limit=50): return []
    def fetch_messages(self, limit=50): return []


def setup(state=ConnectionState.EXECUTION_ENABLED, autonomy=AutonomyMode.EXECUTE, granted=("publish", "lookup", "edit", "delete", "comments")):
    c = IntegrationConnection("tenant-a", "facebook", state, granted, autonomy, connection_id="c1")
    result = certify_facebook_reference(workspace_id="tenant-a").result
    r = register_certification(c, result, now=NOW)
    r = transition_registry(r, RegistryState.ACTIVE, connection=c, now=NOW)
    fake = FakeConnector()
    governed = GovernedConnector(fake, workspace_id="tenant-a", ledger=ExecutionLedger(allow_in_memory=True))
    return c, r, fake, RuntimeConnectorGateway("tenant-a", c, contract(), r, governed)


def test_execute_requires_all_runtime_gates():
    c, r, fake, gateway = setup()
    assert gateway.authorize("publish").allowed is True


def test_allowed_mutation_delegates_to_governed_execution():
    c, r, fake, gateway = setup()
    result = asyncio.run(gateway.publish("hello", idempotency_key="k1"))
    assert result["status"] == "succeeded"
    assert fake.calls == [("publish", "hello")]


def test_observe_connection_cannot_mutate():
    c, r, fake, _ = setup(state=ConnectionState.OBSERVE_ONLY, autonomy=AutonomyMode.OBSERVE)
    gateway = RuntimeConnectorGateway("tenant-a", c, contract(), r, GovernedConnector(fake, workspace_id="tenant-a", ledger=ExecutionLedger(allow_in_memory=True)))
    with pytest.raises(ConnectorRuntimeBlocked) as exc:
        asyncio.run(gateway.publish("hello"))
    assert RuntimeBlock.CONNECTION_NOT_EXECUTION_ENABLED.value in exc.value.authorization.reasons
    assert not fake.calls


def test_suggest_mode_cannot_mutate():
    c, r, fake, gateway = setup(state=ConnectionState.SUGGEST_ENABLED, autonomy=AutonomyMode.SUGGEST)
    with pytest.raises(ConnectorRuntimeBlocked) as exc:
        asyncio.run(gateway.publish("hello"))
    assert RuntimeBlock.AUTONOMY_NOT_EXECUTE.value in exc.value.authorization.reasons
    assert not fake.calls


def test_missing_granted_capability_cannot_mutate():
    c, r, fake, gateway = setup(granted=("lookup",))
    with pytest.raises(ConnectorRuntimeBlocked) as exc:
        asyncio.run(gateway.publish("hello"))
    assert RuntimeBlock.CAPABILITY_NOT_GRANTED.value in exc.value.authorization.reasons
    assert not fake.calls


def test_inactive_registry_cannot_mutate():
    c = IntegrationConnection("tenant-a", "facebook", ConnectionState.EXECUTION_ENABLED,
                              ("publish",), AutonomyMode.EXECUTE, connection_id="c1")
    result = certify_facebook_reference(workspace_id="tenant-a").result
    r = register_certification(c, result, now=NOW)
    fake = FakeConnector()
    gateway = RuntimeConnectorGateway("tenant-a", c, contract(), r,
                                      GovernedConnector(fake, workspace_id="tenant-a", ledger=ExecutionLedger(allow_in_memory=True)))
    with pytest.raises(ConnectorRuntimeBlocked) as exc:
        asyncio.run(gateway.publish("hello"))
    assert RuntimeBlock.REGISTRY_NOT_ACTIVE.value in exc.value.authorization.reasons
    assert not fake.calls


def test_cross_tenant_gateway_construction_fails():
    c, r, fake, _ = setup()
    with pytest.raises(ValueError, match=RuntimeBlock.TENANT_MISMATCH.value):
        RuntimeConnectorGateway("tenant-b", c, contract(), r,
                                GovernedConnector(fake, workspace_id="tenant-a", ledger=ExecutionLedger(allow_in_memory=True)))


def test_version_drift_blocks_before_provider_call():
    c, r, fake, _ = setup()
    drifted = ConnectorContract("facebook", "facebook", "2.0.0", capabilities=contract().capabilities,
                                inbound_events=contract().inbound_events, outbound_actions=contract().outbound_actions,
                                authentication_method="oauth2", risk_by_action=contract().risk_by_action,
                                requires_reconciliation=True, tenant_scoped=True, certified=True)
    gateway = RuntimeConnectorGateway("tenant-a", c, drifted, r,
                                      GovernedConnector(fake, workspace_id="tenant-a", ledger=ExecutionLedger(allow_in_memory=True)))
    with pytest.raises(ConnectorRuntimeBlocked) as exc:
        asyncio.run(gateway.publish("hello"))
    assert RuntimeBlock.VERSION_DRIFT.value in exc.value.authorization.reasons
    assert not fake.calls
