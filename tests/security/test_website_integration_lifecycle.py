import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from application.website_integrations import (
    WebsiteIntegrationApplicationService,
    WebsiteIntegrationContext,
)
from contracts.website_integration import WebsiteEventEnvelope


def _envelope():
    return WebsiteEventEnvelope(
        integration_id="site-a",
        event_id="event-0001",
        event_type="article.created",
        payload={"article_id": "article-1"},
    )


@pytest.mark.parametrize("status", ["revoked", "disconnected", "degraded"])
def test_inactive_website_integration_rejects_connector_events(status):
    port = Mock()
    port.get.return_value = SimpleNamespace(status=status)
    service = WebsiteIntegrationApplicationService(port)

    with pytest.raises(LookupError, match="not active"):
        asyncio.run(
            service.receive_event(
                WebsiteIntegrationContext("ws-1"), "site-a", _envelope()
            )
        )
    port.record_event.assert_not_called()


def test_configured_website_integration_accepts_registered_event():
    port = Mock()
    port.get.return_value = SimpleNamespace(status="configured")
    port.event_type_is_registered.return_value = True
    port.record_event.return_value = {"accepted": True, "duplicate": False}
    service = WebsiteIntegrationApplicationService(port)

    result = asyncio.run(
        service.receive_event(
            WebsiteIntegrationContext("ws-1"), "site-a", _envelope()
        )
    )
    assert result["accepted"] is True
    port.record_event.assert_called_once()
