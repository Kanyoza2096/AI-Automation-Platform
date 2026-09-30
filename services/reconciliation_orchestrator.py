"""Bounded orchestration for ambiguous external side effects.

This layer deliberately resolves only provider-authoritative outcomes. It never
creates a retry from a transport error or a single eventually-consistent miss.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional

from services.external_reconciliation import ExternalReconciler, ReconciliationResult

logger = logging.getLogger("kanyoza.reconciliation_orchestrator")

@dataclass(frozen=True)
class ReconciliationPolicy:
    max_attempts: int = 5
    not_found_confirmations: int = 2

class ReconciliationOrchestrator:
    def __init__(self, policy: Optional[ReconciliationPolicy] = None):
        self.policy = policy or ReconciliationPolicy()

    def reconcile_item(self, item: Any, connector: Any) -> ReconciliationResult:
        attempts = int(getattr(item, "reconciliation_attempts", 0) or 0)
        if attempts >= self.policy.max_attempts:
            return ReconciliationResult("unknown", getattr(item, "platform_post_id", None), "Reconciliation attempt budget exhausted; operator review required")
        return ExternalReconciler().reconcile(item, connector=connector)

    def reconcile_many(self, items: Iterable[Any], connector_resolver: Callable[[Any], Any]) -> dict[str, int]:
        stats = {"checked": 0, "published": 0, "requeued": 0, "uncertain": 0, "skipped": 0}
        for item in items:
            if getattr(item, "status", None) != "uncertain":
                stats["skipped"] += 1
                continue
            stats["checked"] += 1
            connector = connector_resolver(item)
            if connector is None:
                stats["uncertain"] += 1
                continue
            result = self.reconcile_item(item, connector)
            if result.status == "published":
                stats["published"] += 1
            elif result.status == "not_found":
                # State transition is applied by PublishOutbox, which owns
                # durable counters and persistence.
                stats["requeued"] += 1
            else:
                stats["uncertain"] += 1
        return stats
