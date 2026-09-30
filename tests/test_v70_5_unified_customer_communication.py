from services.v70_5_unified_customer_communication import (
    CommunicationDisposition,
    CustomerCommunicationRequest,
    UnifiedCustomerCommunication,
)


def _request(**overrides):
    data = dict(
        workspace_id="ws-1",
        customer_id="cust-1",
        event_id="evt-1",
        correlation_id="corr-1",
        intent="follow_up",
        message="Thanks for your interest.",
        channel="email",
        recipient="customer@example.com",
    )
    data.update(overrides)
    return CustomerCommunicationRequest(**data)


def test_ready_communication_is_governed_not_sent():
    admission = UnifiedCustomerCommunication().admit(_request())
    assert admission.disposition == CommunicationDisposition.READY_FOR_GOVERNANCE
    assert admission.governed_action == "customer_communication"
    assert admission.sendable is True


def test_opt_out_suppresses_communication():
    runtime = UnifiedCustomerCommunication()
    runtime.suppress_customer("cust-1")
    admission = runtime.admit(_request())
    assert admission.disposition == CommunicationDisposition.SUPPRESSED
    assert admission.sendable is False


def test_invalid_whatsapp_requires_account():
    admission = UnifiedCustomerCommunication().admit(_request(channel="whatsapp"))
    assert admission.disposition == CommunicationDisposition.INVALID


def test_communication_identity_is_deterministic():
    a = _request().communication_id
    b = _request().communication_id
    assert a == b
    assert a.startswith("kdo:comm:v70.5:")


def test_build_event_preserves_governance_boundary():
    event = UnifiedCustomerCommunication().build_event(_request())
    assert event["governed_action"] == "customer_communication"
    assert event["communication_disposition"] == "ready_for_governance"
