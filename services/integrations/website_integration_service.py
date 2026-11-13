"""Durable registry and event adapter for Next.js -> KDO Website Connector integrations.

This service deliberately does not execute website mutations. WebsiteConnector remains
KDO's sole website external-world adapter; governed execution still flows through the
existing Action Kernel/outbox path.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

from models.social_account import SocialAccount, Platform
from repositories.social_account_repository import SocialAccountRepository

from contracts.website_integration import (
    WebsiteCapability,
    WebsiteEventEnvelope,
    WebsiteIntegrationReadModel,
    WebsiteIntegrationRegistration,
    WebsiteIntegrationStatus,
)

logger = logging.getLogger("kanyoza.integrations.website")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WebsiteIntegrationService:
    TABLE = "website_integrations"

    def __init__(self, supabase_client: Any) -> None:
        self.db = supabase_client
        if self.db is None:
            raise RuntimeError("Durable database client is required")

    def register(self, workspace_id: str, registration: WebsiteIntegrationRegistration, website_api_token: str = "") -> WebsiteIntegrationReadModel:
        row = {
            "integration_id": registration.integration_id,
            "workspace_id": workspace_id,
            "app_name": registration.app_name,
            "environment": registration.environment,
                "status": registration.publish_status,
            "base_url": registration.base_url,
            "publish_path": registration.publish_path,
            "lookup_path_template": registration.lookup_path_template,
            "edit_path_template": registration.edit_path_template,
            "delete_path_template": registration.delete_path_template,
            "capabilities": [x.value for x in registration.capabilities],
            "event_types": [x.value for x in registration.event_types],
            "status": WebsiteIntegrationStatus.CONFIGURED.value,
            "secret_hash": None,
            "created_at": _now(),
            "updated_at": _now(),
        }
        if not website_api_token:
            raise ValueError("website_api_token is required to configure the Website Connector")
        required_capability = WebsiteCapability.PUBLISH_ARTICLE if registration.publish_status == "publish" else WebsiteCapability.CREATE_DRAFT
        if required_capability not in registration.capabilities:
            raise ValueError(f"publish_status '{registration.publish_status}' requires capability '{required_capability.value}'")
        result = self.db.table(self.TABLE).upsert(row, on_conflict="workspace_id,integration_id").execute()
        # The existing WebsiteConnector is the only website mutation adapter.
        # Registration therefore creates/updates its canonical website account
        # rather than introducing a second executor.
        account = SocialAccount(
            id=f"website-{registration.integration_id}",
            workspace_id=workspace_id,
            platform=Platform.WEBSITE,
            platform_account_id=registration.integration_id,
            account_name=registration.app_name,
            access_token=website_api_token,
            platform_config={
                "base_url": registration.base_url + "/",
                "publish_path": registration.publish_path,
                "lookup_path_template": registration.lookup_path_template,
                "edit_path_template": registration.edit_path_template,
                "delete_path_template": registration.delete_path_template,
                "integration_id": registration.integration_id,
                "environment": registration.environment,
            },
            enabled=True,
        )
        SocialAccountRepository(self.db).save(account)
        if not result.data:
            raise RuntimeError("Website integration was not persisted")
        return self._read(result.data[0])

    def get(self, workspace_id: str, integration_id: str) -> WebsiteIntegrationReadModel | None:
        result = (self.db.table(self.TABLE).select("*").eq("workspace_id", workspace_id)
                  .eq("integration_id", integration_id).maybe_single().execute())
        return self._read(result.data) if getattr(result, "data", None) else None

    def list(self, workspace_id: str) -> list[WebsiteIntegrationReadModel]:
        result = self.db.table(self.TABLE).select("*").eq("workspace_id", workspace_id).order("created_at", desc=True).execute()
        return [self._read(row) for row in (result.data or [])]

    def event_is_registered(self, workspace_id: str, integration_id: str) -> bool:
        return self.get(workspace_id, integration_id) is not None

    def event_type_is_registered(self, workspace_id: str, integration_id: str, event_type: str) -> bool:
        integration = self.get(workspace_id, integration_id)
        return integration is not None and event_type in {item.value for item in integration.event_types}

    def record_event(self, workspace_id: str, envelope: WebsiteEventEnvelope) -> dict[str, Any]:
        integration = self.get(workspace_id, envelope.integration_id)
        if integration is None:
            raise LookupError("Website integration not found")
        # Durable idempotency: the event ledger rejects duplicate delivery IDs.
        row = {
            "workspace_id": workspace_id,
            "integration_id": envelope.integration_id,
            "event_id": envelope.event_id,
            "event_type": envelope.event_type.value,
            "event_version": envelope.event_version,
            "occurred_at": envelope.occurred_at.isoformat(),
            "payload": envelope.payload,
            "received_at": _now(),
        }
        try:
            result = self.db.table("website_integration_events").insert(row).execute()
        except Exception as exc:
            if "duplicate" in str(exc).lower() or "unique" in str(exc).lower():
                return {"accepted": True, "duplicate": True, "event_id": envelope.event_id}
            raise
        if not result.data:
            raise RuntimeError("Website integration event was not persisted")
        self.db.table(self.TABLE).update({"last_event_at": _now(), "updated_at": _now()}) \
            .eq("workspace_id", workspace_id).eq("integration_id", envelope.integration_id).execute()
        return {"accepted": True, "duplicate": False, "event_id": envelope.event_id}

    def _read(self, row: dict[str, Any]) -> WebsiteIntegrationReadModel:
        return WebsiteIntegrationReadModel(
            integration_id=str(row["integration_id"]),
            workspace_id=str(row["workspace_id"]),
            app_name=str(row.get("app_name") or ""),
            environment=str(row.get("environment") or "production"),
            status=WebsiteIntegrationStatus(str(row.get("status") or "registered")),
            capabilities=list(row.get("capabilities") or []),
            event_types=list(row.get("event_types") or []),
            last_event_at=row.get("last_event_at"),
            last_health_at=row.get("last_health_at"),
            last_health_ok=row.get("last_health_ok"),
        )

    @staticmethod
    def _hash_secret(secret: str) -> str:
        return hashlib.sha256(secret.encode("utf-8")).hexdigest()
