"""Provider-authoritative reconciliation from asynchronous webhook receipts."""
from __future__ import annotations

from typing import Any, Optional

from core.publish_outbox import PublishOutbox


class ProviderReconciliationService:
    """Translate provider webhook status into conservative outbox state."""

    TERMINAL_SUCCESS = {"sent", "delivered", "read", "published", "finished"}
    TERMINAL_FAILURE = {"failed", "error", "rejected"}

    def __init__(self, outbox: Optional[PublishOutbox] = None):
        self.outbox = outbox or PublishOutbox()

    def apply_event(
        self,
        *,
        platform: str,
        provider_post_id: str,
        status: str,
        workspace_id: Optional[str] = None,
        evidence: str = "",
    ) -> dict[str, Any]:
        item = self.outbox.reconcile_from_provider_event(
            platform=platform,
            provider_post_id=provider_post_id,
            provider_status=status,
            workspace_id=workspace_id,
            evidence=evidence,
        )
        return {
            "matched": item is not None,
            "outbox_id": item.id if item else None,
            "status": item.status if item else "unmatched",
            "provider_status": status,
        }
