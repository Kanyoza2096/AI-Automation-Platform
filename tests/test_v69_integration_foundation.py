import pytest
from services.business_integration_platform import ConnectorContract, ConnectionState, IntegrationConnection
from services.v69_integration_foundation import (
    CredentialBinding, CredentialKind, EventMapping, FieldMapping,
    discover_capabilities, revoke_credential, validate_credential_binding,
    validate_event_mapping,
)


def conn(state=ConnectionState.OBSERVE_ONLY, tenant="tenant-a"):
    return IntegrationConnection(tenant, "crm.demo", state, ("crm.read_customer",), connection_id="int_a")


def contract():
    return ConnectorContract(
        "crm.demo", "demo-crm", "1.0",
        capabilities=("crm.read_customer", "crm.create_followup"),
        inbound_events=("lead.created",), outbound_actions=("create_followup",), certified=True,
    )


def test_credential_binding_is_opaque_and_tenant_bound():
    binding = CredentialBinding("tenant-a", "int_a", CredentialKind.OAUTH, "vault://secret/123", "acct-7")
    status = validate_credential_binding(conn(), binding)
    assert status.safe_for_discovery
    with pytest.raises(ValueError):
        CredentialBinding("tenant-a", "int_a", CredentialKind.OAUTH, "Bearer abc")
    with pytest.raises(ValueError):
        validate_credential_binding(conn("observe_only", "tenant-b"), binding)


def test_revocation_never_changes_authority_but_disables_binding():
    binding = CredentialBinding("tenant-a", "int_a", CredentialKind.API_KEY, "vault://secret/123")
    revoked = revoke_credential(binding)
    assert revoked.active is False
    assert revoked.secret_ref == binding.secret_ref


def test_discovery_cannot_escalate_connector_capabilities():
    result = discover_capabilities(conn(), contract(), observed_capabilities=("crm.read_customer",))
    assert result.ready
    with pytest.raises(ValueError):
        discover_capabilities(conn(), contract(), observed_capabilities=("crm.delete_customer",))


def test_discovery_rejects_undeclared_event_and_action():
    with pytest.raises(ValueError):
        discover_capabilities(conn(), contract(), observed_events=("customer.deleted",))
    with pytest.raises(ValueError):
        discover_capabilities(conn(), contract(), observed_actions=("delete_customer",))


def test_event_mapping_is_tenant_bound_and_requires_stable_event_id():
    mapping = EventMapping(
        "tenant-a", "int_a", "lead.created", "customer.lead.created",
        fields=(FieldMapping("customer.name", "lead.name", True),),
        stable_event_id_field="id",
    )
    validate_event_mapping(mapping, conn(ConnectionState.MAPPING_REQUIRED), contract())
    assert mapping.canonical_payload({"id": "evt-1", "lead": {"name": "A"}}) == {
        "customer.name": "A", "event_id": "evt-1"
    }
    with pytest.raises(ValueError):
        mapping.canonical_payload({"lead": {"name": "A"}})


def test_mapping_cannot_cross_connection_or_tenant():
    mapping = EventMapping("tenant-a", "int_a", "lead.created", "customer.lead.created")
    with pytest.raises(ValueError):
        validate_event_mapping(mapping, conn(ConnectionState.MAPPING_REQUIRED, "tenant-b"), contract())
    with pytest.raises(ValueError):
        validate_event_mapping(mapping, IntegrationConnection("tenant-a", "crm.demo", ConnectionState.MAPPING_REQUIRED, connection_id="int-b"), contract())
