import hashlib, hmac, time
import pytest

from services.business_integration_platform import ConnectorContract, IntegrationConnection, ConnectionState, AutonomyMode
from services.v69_integration_foundation import EventMapping, FieldMapping
from services.v69_connector_event_gateway import (
    ConnectorEventRuntimeGateway, MemoryReplayFence, InboundAuthentication,
    IngressBlock, verify_hmac_sha256,
)


def make():
    contract = ConnectorContract(
        connector_key="facebook", provider="facebook", version="1",
        capabilities=("publish",), inbound_events=("lead.created",), outbound_actions=("publish",),
        authentication_method="oauth", risk_by_action={"publish":"high"},
        requires_reconciliation=True, tenant_scoped=True, certified=True,
    )
    conn = IntegrationConnection(
        workspace_id="w1", connector_key="facebook", state=ConnectionState.OBSERVE_ONLY,
        granted_capabilities=("publish",), autonomy_mode=AutonomyMode.OBSERVE, connection_id="c1",
    )
    mapping = EventMapping(
        "w1", "c1", "lead.created", "customer.lead.created",
        fields=(FieldMapping("email", "lead.email", True),),
        tenant_field="workspace_id", stable_event_id_field="id",
    )
    return ConnectorEventRuntimeGateway("w1", conn, contract, mapping, MemoryReplayFence())


def auth(g, body=b'{"id":"e1","lead":{"email":"a@b.test"}}'):
    secret="secret"
    sig="sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return g.authenticate_hmac(body=body, signature=sig, tenant_claim="w1", secret=secret)


def test_signature_is_verified():
    body=b'abc'; secret='s'; sig=hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_hmac_sha256(secret, body, sig)
    assert not verify_hmac_sha256(secret, body, 'bad')


def test_valid_event_is_canonicalized_and_fenced():
    g=make(); a=auth(g)
    r=g.ingest(external_event="lead.created", payload={"id":"e1","lead":{"email":"a@b.test"}}, authentication=a)
    assert r.accepted and r.event.canonical_event == "customer.lead.created"
    assert r.event.payload["email"] == "a@b.test"
    again=g.ingest(external_event="lead.created", payload={"id":"e1","lead":{"email":"a@b.test"}}, authentication=a)
    assert not again.accepted and again.replay and again.reasons == (IngressBlock.REPLAY.value,)


def test_cross_tenant_claim_is_rejected():
    g=make(); body=b'x'; secret='secret'; sig=hmac.new(secret.encode(),body,hashlib.sha256).hexdigest()
    a=g.authenticate_hmac(body=body, signature=sig, tenant_claim="w2", secret=secret)
    r=g.ingest(external_event="lead.created", payload={"id":"e2","lead":{"email":"x@y.test"}}, authentication=a)
    assert not r.accepted and IngressBlock.AUTHENTICATION_REQUIRED.value in r.reasons


def test_invalid_signature_never_enters_canonical_plane():
    g=make(); a=InboundAuthentication(False, False, False)
    r=g.ingest(external_event="lead.created", payload={"id":"e3","lead":{"email":"x@y.test"}}, authentication=a)
    assert not r.accepted and IngressBlock.AUTHENTICATION_REQUIRED.value in r.reasons


def test_undeclared_event_is_rejected():
    g=make(); r=g.ingest(external_event="order.created", payload={"id":"e4","lead":{"email":"x@y.test"}}, authentication=auth(g))
    assert not r.accepted and IngressBlock.EVENT_NOT_DECLARED.value in r.reasons


def test_stale_event_is_rejected():
    g=make(); r=g.ingest(external_event="lead.created", payload={"id":"e5","lead":{"email":"x@y.test"}}, authentication=auth(g), received_at=time.time()-301)
    assert not r.accepted and IngressBlock.STALE_EVENT.value in r.reasons


def test_revoked_connection_is_rejected():
    g=make(); object.__setattr__(g.connection, 'state', ConnectionState.REVOKED)
    r=g.ingest(external_event="lead.created", payload={"id":"e6","lead":{"email":"x@y.test"}}, authentication=auth(g))
    assert not r.accepted and IngressBlock.CONNECTION_REVOKED.value in r.reasons


def test_missing_stable_id_is_rejected():
    g=make(); r=g.ingest(external_event="lead.created", payload={"lead":{"email":"x@y.test"}}, authentication=auth(g))
    assert not r.accepted and IngressBlock.MISSING_EVENT_ID.value in r.reasons
