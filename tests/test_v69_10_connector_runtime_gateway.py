import asyncio
from datetime import datetime, timezone

import pytest

from services.business_integration_platform import AutonomyMode, ConnectionState, ConnectorContract, IntegrationConnection
from services.connectors.governed_connector import GovernedConnector
from services.execution_ledger import ExecutionLedger
from services.v69_connector_activation_registry import RegistryState, register_certification, transition_registry
from services.v69_connector_runtime_gateway import ConnectorRuntimeBlocked, RuntimeBlock, RuntimeConnectorGateway
from services.v69_reference_connector_certification import certify_facebook_reference

NOW = datetime.now(timezone.utc)


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
    def __init__(self):
        self.calls = []
        self.health_calls = 0
    def publish(self, content, **kwargs): self.calls.append(("publish", content)); return {"ok": True, "post_id": "p1"}
    def edit(self, post_id, content): self.calls.append(("edit", post_id, content)); return {"ok": True, "post_id": post_id}
    def delete(self, post_id): self.calls.append(("delete", post_id)); return {"ok": True, "post_id": post_id}
    def lookup_post(self, post_id): return None
    def health(self):
        self.health_calls += 1
        return {"ok": True, "platform": "facebook", "latency_ms": 12, "status_code": 200}
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


class FakeHealthObservationStore:
    def __init__(self):
        self.calls = []

    def record_health_observation(self, **kwargs):
        self.calls.append(kwargs)
        return {"persisted": True, **kwargs}


def test_tenant_gateway_records_health_with_its_bound_workspace_and_connection():
    c, r, fake, gateway = setup()
    store = FakeHealthObservationStore()

    result = gateway.observe_health(store, observed_at=NOW)

    assert result["persisted"] is True
    assert store.calls[0]["workspace_id"] == "tenant-a"
    assert store.calls[0]["connection_id"] == "c1"
    assert store.calls[0]["connector_key"] == "facebook"
    assert store.calls[0]["health_state"] == "healthy"
    assert store.calls[0]["provider_status_code"] == 200
    assert store.calls[0]["latency_ms"] == 12
    assert store.calls[0]["observed_at"] == NOW
    assert fake.health_calls == 1
    assert fake.calls == []


def test_tenant_gateway_records_unhealthy_probe_as_unknown_without_raw_error():
    class UnhealthyConnector(FakeConnector):
        def health(self):
            self.health_calls += 1
            return {
                "ok": False,
                "platform": "facebook",
                "latency_ms": 7,
                "error": "token_invalid_do_not_persist",
            }

    c, r, fake, _ = setup()
    connector = UnhealthyConnector()
    gateway = RuntimeConnectorGateway(
        "tenant-a", c, contract(), r,
        GovernedConnector(connector, workspace_id="tenant-a", ledger=ExecutionLedger(allow_in_memory=True)),
    )
    store = FakeHealthObservationStore()

    gateway.observe_health(store, observed_at=NOW)

    call = store.calls[0]
    assert call["health_state"] == "unknown"
    assert call["reason_codes"] == ("provider_check_not_healthy",)
    assert "token_invalid_do_not_persist" not in repr(call)
    assert connector.health_calls == 1
    assert connector.calls == []


def test_tenant_gateway_requires_explicit_durable_observation_store():
    c, r, fake, gateway = setup()

    with pytest.raises(ValueError, match="durable_health_observation_store_required"):
        gateway.observe_health(None, observed_at=NOW)

    assert fake.health_calls == 0


def test_tenant_gateway_rejects_registry_connection_mismatch_before_probe():
    from dataclasses import replace

    c, r, fake, _ = setup()
    mismatched_registry = replace(r, connection_id="another-connection")
    gateway = RuntimeConnectorGateway(
        "tenant-a", c, contract(), mismatched_registry,
        GovernedConnector(fake, workspace_id="tenant-a", ledger=ExecutionLedger(allow_in_memory=True)),
    )

    with pytest.raises(ValueError, match=RuntimeBlock.TENANT_MISMATCH.value):
        gateway.observe_health(FakeHealthObservationStore(), observed_at=NOW)

    assert fake.health_calls == 0


def test_tenant_gateway_rejects_invalid_health_result_without_persisting():
    class InvalidHealthConnector(FakeConnector):
        def health(self):
            self.health_calls += 1
            return {"error": "missing_ok"}

    c, r, fake, _ = setup()
    connector = InvalidHealthConnector()
    gateway = RuntimeConnectorGateway(
        "tenant-a", c, contract(), r,
        GovernedConnector(connector, workspace_id="tenant-a", ledger=ExecutionLedger(allow_in_memory=True)),
    )
    store = FakeHealthObservationStore()

    with pytest.raises(ValueError, match="provider_health_result_invalid"):
        gateway.observe_health(store, observed_at=NOW)

    assert store.calls == []
    assert connector.health_calls == 1
