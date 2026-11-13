from pathlib import Path

import asyncio
from types import SimpleNamespace

from application.website_integrations import WebsiteIntegrationApplicationService, WebsiteIntegrationContext
from contracts.website_integration import WebsiteEventEnvelope, WebsiteEventType


class Port:
    def __init__(self): self.events=[]; self.integrations={("ws-1","site-1"): {"integration_id":"site-1","status":"configured"}}
    def list(self, workspace_id): return [self.integrations[(workspace_id,"site-1")]]
    def get(self, workspace_id, integration_id): return self.integrations.get((workspace_id,integration_id))
    def register(self, workspace_id, registration, website_api_token=""): return {"workspace_id":workspace_id,"integration_id":registration.integration_id}
    def event_is_registered(self, workspace_id, integration_id): return (workspace_id,integration_id) in self.integrations
    def event_type_is_registered(self, workspace_id, integration_id, event_type): return event_type == "article.published" and (workspace_id,integration_id) in self.integrations
    def record_event(self, workspace_id, envelope): self.events.append((workspace_id,envelope.event_id)); return {"accepted":True,"duplicate":False,"event_id":envelope.event_id}


class Publisher:
    def __init__(self): self.calls=[]
    async def publish(self, event_type, *, payload, source, workspace_id): self.calls.append((event_type,payload,source,workspace_id))


def test_context_rejects_missing_workspace():
    try: WebsiteIntegrationContext(" ").validate()
    except ValueError as exc: assert "workspace" in str(exc)
    else: raise AssertionError("missing workspace accepted")


def test_application_service_keeps_event_publication_outside_http_adapter():
    port, publisher = Port(), Publisher()
    svc = WebsiteIntegrationApplicationService(port, publisher)
    envelope = WebsiteEventEnvelope(integration_id="site-1", event_id="evt-12345678", event_type=WebsiteEventType.ARTICLE_PUBLISHED, payload={"id":"p1"})
    result = asyncio.run(svc.receive_event(WebsiteIntegrationContext("ws-1"), "site-1", envelope))
    assert result["accepted"]
    assert publisher.calls[0][0] == "website.integration.article.published"
    assert publisher.calls[0][3] == "ws-1"


def test_application_service_rejects_cross_integration_event():
    svc = WebsiteIntegrationApplicationService(Port())
    envelope = WebsiteEventEnvelope(integration_id="site-1", event_id="evt-12345679", event_type=WebsiteEventType.ARTICLE_PUBLISHED, payload={})
    try: asyncio.run(svc.receive_event(WebsiteIntegrationContext("ws-1"), "site-2", envelope))
    except ValueError as exc: assert "mismatch" in str(exc)
    else: raise AssertionError("mismatched integration accepted")


def test_website_route_does_not_construct_infrastructure():
    root = Path(__file__).resolve().parents[2]
    source = (root / "api" / "website_integrations.py").read_text(encoding="utf-8")
    assert "website_integration_application" in source
    assert "services.integrations" not in source
    assert "services.supabase" not in source
    assert "core.unified_bus" not in source


def test_application_service_rejects_event_type_not_enabled_for_integration():
    port = Port()
    svc = WebsiteIntegrationApplicationService(port)
    envelope = WebsiteEventEnvelope(
        integration_id="site-1", event_id="evt-12345680",
        event_type=WebsiteEventType.ARTICLE_CREATED, payload={"id": "p2"},
    )
    try:
        asyncio.run(svc.receive_event(WebsiteIntegrationContext("ws-1"), "site-1", envelope))
    except ValueError as exc:
        assert "not enabled" in str(exc)
    else:
        raise AssertionError("unregistered event type accepted")
