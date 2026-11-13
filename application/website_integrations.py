"""Application use cases for website integrations.

HTTP, persistence, and connector-account details stay outside this layer.
The application service owns workspace-scoped use-case orchestration.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from contracts.website_integration import WebsiteEventEnvelope, WebsiteIntegrationRegistration


class WebsiteEventPublisherPort(Protocol):
    async def publish(self, event_type: str, *, payload: Mapping[str, Any], source: str, workspace_id: str) -> Any: ...


class WebsiteIntegrationPort(Protocol):
    def register(self, workspace_id: str, registration: WebsiteIntegrationRegistration, website_api_token: str = "") -> Any: ...
    def get(self, workspace_id: str, integration_id: str) -> Any: ...
    def list(self, workspace_id: str) -> list[Any]: ...
    def event_is_registered(self, workspace_id: str, integration_id: str) -> bool: ...
    def event_type_is_registered(self, workspace_id: str, integration_id: str, event_type: str) -> bool: ...
    def record_event(self, workspace_id: str, envelope: WebsiteEventEnvelope) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class WebsiteIntegrationContext:
    workspace_id: str

    def validate(self) -> str:
        workspace_id = self.workspace_id.strip()
        if not workspace_id:
            raise ValueError("authenticated workspace scope required")
        return workspace_id


class WebsiteIntegrationApplicationService:
    """Workspace-scoped application orchestration for website integrations."""

    def __init__(self, port: WebsiteIntegrationPort, event_publisher: WebsiteEventPublisherPort | None = None):
        self._port = port
        self._event_publisher = event_publisher

    def list(self, context: WebsiteIntegrationContext) -> list[Any]:
        return self._port.list(context.validate())

    def register(
        self,
        context: WebsiteIntegrationContext,
        registration: WebsiteIntegrationRegistration,
        website_api_token: str,
    ) -> Any:
        return self._port.register(context.validate(), registration, website_api_token)

    def get(self, context: WebsiteIntegrationContext, integration_id: str) -> Any:
        return self._port.get(context.validate(), integration_id)

    async def receive_event(self, context: WebsiteIntegrationContext, integration_id: str, envelope: WebsiteEventEnvelope) -> Mapping[str, Any]:
        workspace_id = context.validate()
        if envelope.integration_id != integration_id:
            raise ValueError("integration_id mismatch")
        integration = self._port.get(workspace_id, integration_id)
        if integration is None:
            raise LookupError("Website integration not found")
        status = getattr(getattr(integration, "status", None), "value", getattr(integration, "status", None))
        if status not in ("configured", "connected"):
            raise LookupError("Website integration is not active")
        if not self._port.event_type_is_registered(workspace_id, integration_id, envelope.event_type.value):
            raise ValueError("event_type is not enabled for this website integration")
        result = self._port.record_event(workspace_id, envelope)
        if self._event_publisher is not None:
            await self._event_publisher.publish(
                f"website.integration.{envelope.event_type.value}",
                payload={"integration_id": integration_id, "event_id": envelope.event_id, "payload": envelope.payload},
                source="nextjs-website-integration",
                workspace_id=workspace_id,
            )
        return result
