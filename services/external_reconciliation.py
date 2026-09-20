"""Conservative reconciliation of ambiguous external publish side effects.

Never infers publication from timing, caption similarity, or a generic HTTP error.
A publish may be auto-resolved only when a provider can positively verify a known
provider object id. Otherwise the item remains UNCERTAIN for operator review.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger("kanyoza.external_reconciliation")


@dataclass(frozen=True)
class ReconciliationResult:
    status: str  # published | not_found | unknown | error
    post_id: Optional[str] = None
    evidence: Optional[str] = None


class ExternalReconciler:
    """Provider-aware, fail-closed reconciliation adapter."""

    def __init__(self, connector_factory=None):
        self._factory = connector_factory

    def _connector(self, item: Any):
        from services.connectors.connector_factory import create_connector
        account = getattr(item, "social_account", None)
        if account is None:
            return None
        return create_connector(account)

    def reconcile(self, item: Any, connector=None) -> ReconciliationResult:
        post_id = getattr(item, "platform_post_id", None)
        if not post_id:
            return ReconciliationResult("unknown", evidence="No provider object id is available")
        connector = connector or self._connector(item)
        if connector is None:
            return ReconciliationResult("unknown", evidence="No connector/account available")
        lookup = getattr(connector, "lookup_post", None)
        if not callable(lookup):
            return ReconciliationResult("unknown", evidence=f"{item.platform} connector has no authoritative lookup")
        try:
            result = lookup(str(post_id))
            if result is True:
                return ReconciliationResult("published", str(post_id), "Provider lookup positively confirmed object")
            if result is False:
                return ReconciliationResult("not_found", str(post_id), "Provider lookup returned object-not-found")
            if isinstance(result, dict):
                state = str(result.get("status", "unknown")).lower()
                if state == "published":
                    return ReconciliationResult("published", str(result.get("post_id") or post_id), "Provider lookup positively confirmed publication")
                if state == "not_found":
                    return ReconciliationResult("not_found", str(post_id), "Provider lookup returned object-not-found")
            return ReconciliationResult("unknown", str(post_id), "Provider response was inconclusive")
        except Exception as exc:
            logger.warning("External reconciliation failed platform=%s id=%s: %s", item.platform, post_id, exc)
            return ReconciliationResult("error", str(post_id), str(exc)[:300])
