from services.business_integration_platform import *
from services.v69_business_onboarding import assess_onboarding


def connector(certified=True):
    return ConnectorContract(
        connector_key="crm.demo", provider="demo-crm", version="1.0",
        capabilities=("crm.read_customer", "crm.create_followup"),
        inbound_events=("lead.created",), outbound_actions=("create_followup",),
        certified=certified,
    )


def test_business_identity_is_tenant_bound_and_fingerprinted():
    b = BusinessOperatingIdentity("tenant-a", "ABC", objectives=("sales",))
    assert b.fingerprint()
    assert b.canonical_dict()["workspace_id"] == "tenant-a"


def test_connector_contract_requires_tenant_scope():
    try:
        ConnectorContract("x", "p", "1", tenant_scoped=False)
        assert False
    except ValueError:
        assert True


def test_connection_execute_requires_execution_state():
    try:
        IntegrationConnection("tenant-a", "crm.demo", autonomy_mode=AutonomyMode.EXECUTE)
        assert False
    except ValueError:
        assert True


def test_objective_capabilities_ignore_other_tenant():
    a = IntegrationConnection("tenant-a", "crm.demo", ConnectionState.OBSERVE_ONLY,
                               ("crm.read_customer",))
    b = IntegrationConnection("tenant-b", "crm.demo", ConnectionState.OBSERVE_ONLY,
                               ("crm.create_followup",))
    result = validate_objective_capabilities("tenant-a", ("crm.create_followup",), (a, b))
    assert result["ready"] is False
    assert result["missing"] == ["crm.create_followup"]


def test_onboarding_starts_at_connect():
    identity = BusinessOperatingIdentity("tenant-a", "ABC")
    result = assess_onboarding(identity, (), (connector(),))
    assert result.stage == "connect"
    assert not result.ready


def test_onboarding_observe_is_ready_but_not_execution():
    identity = BusinessOperatingIdentity(
        "tenant-a", "ABC", objectives=("sales",),
        metadata={"objective_capabilities": {"sales": ["crm.read_customer"]}},
    )
    connection = IntegrationConnection("tenant-a", "crm.demo", ConnectionState.OBSERVE_ONLY,
                                       ("crm.read_customer",))
    result = assess_onboarding(identity, (connection,), (connector(),))
    assert result.stage == "observe"
    assert result.ready


def test_valid_transition_requires_certification_for_execution():
    base = IntegrationConnection("tenant-a", "crm.demo", ConnectionState.SUGGEST_ENABLED,
                                 ("crm.read_customer",), AutonomyMode.SUGGEST)
    nxt = certify_connection_transition(base, ConnectionState.EXECUTION_ENABLED,
                                        connector=connector(), certification_ok=True)
    assert nxt.state == ConnectionState.EXECUTION_ENABLED
    assert nxt.autonomy_mode == AutonomyMode.EXECUTE


def test_invalid_execution_transition_fails_closed():
    base = IntegrationConnection("tenant-a", "crm.demo", ConnectionState.OBSERVE_ONLY,
                                 ("crm.read_customer",))
    try:
        certify_connection_transition(base, ConnectionState.EXECUTION_ENABLED,
                                      connector=connector(), certification_ok=True)
        assert False
    except ValueError:
        assert True
