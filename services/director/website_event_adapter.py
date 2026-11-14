"""Workspace-scoped GrowthBridge event adapter for the KDO Director.

Website events are change notifications, not execution instructions. This
adapter verifies the durable integration receipt, records a bounded Director
observation and an idempotent audit decision, and deliberately creates no task.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import threading
from collections import OrderedDict
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from contracts.website_integration import WebsiteEventType
from services.director.analysis import FunctionObserver
from services.director.models import (
    Authority,
    Decision,
    Health,
    Observation,
    Risk,
    Sector,
)
from services.director.utils import iso, safe

logger = logging.getLogger("kanyoza.director.website_events")


class DirectorWebsiteEventAdapter:
    """Bridge authorized website change events into Director's evidence model."""

    SOURCE = "nextjs-website-integration"
    OBSERVER_NAME = "website_integration_events"
    MAX_RETAINED_EVENTS = 50
    EVENT_NAMES = tuple(
        f"website.integration.{event_type.value}" for event_type in WebsiteEventType
    )

    def __init__(self, director: Any, event_bus: Any, supabase_client: Any):
        if director is None or event_bus is None or supabase_client is None:
            raise ValueError("director, event_bus, and service-role Supabase client are required")
        self.director = director
        self.event_bus = event_bus
        self.supabase = supabase_client
        self._unsubscribers: list[Any] = []
        self._latest_events: OrderedDict[str, dict] = OrderedDict()
        self._latest_lock = threading.RLock()
        self._observer = FunctionObserver(
            self.OBSERVER_NAME, Sector.BRANDS, self.observe
        )
        self._started = False

    def register_observer(self) -> None:
        """Register before Director.start() so its first cycle sees this source."""
        self.director.register_observer(self._observer)

    async def start(self) -> None:
        if self._started:
            return
        try:
            # Director's registry is process-wide. Rehydrate only the bounded,
            # persisted website-event observations before accepting new events.
            await asyncio.to_thread(self._hydrate_recent_events)
            for event_name in self.EVENT_NAMES:
                unsubscribe = self.event_bus.subscribe(event_name, self.handle_event)
                if inspect.isawaitable(unsubscribe):
                    unsubscribe = await unsubscribe
                if callable(unsubscribe):
                    self._unsubscribers.append(unsubscribe)
            self._started = True
        except Exception:
            await self.stop()
            raise
        logger.info(
            "Director website event adapter subscribed to %d contract event types",
            len(self._unsubscribers),
        )

    async def stop(self) -> None:
        for unsubscribe in reversed(self._unsubscribers):
            try:
                result = unsubscribe()
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logger.warning("Director website event unsubscribe failed", exc_info=True)
        self._unsubscribers.clear()
        self._started = False

    def observe(self) -> Observation:
        with self._latest_lock:
            events = list(self._latest_events.values())[-self.MAX_RETAINED_EVENTS:]
        latest = events[-1] if events else None
        stamp = (
            datetime.fromisoformat(latest["observed_at"].replace("Z", "+00:00"))
            if latest else datetime.now(timezone.utc)
        )
        event_type_counts: dict[str, int] = {}
        for event in events:
            event_type = str(event.get("event_type") or "unknown")
            event_type_counts[event_type] = event_type_counts.get(event_type, 0) + 1
        return Observation(
            source=self.OBSERVER_NAME,
            sector=Sector.BRANDS,
            # An external change notification is not a health assertion.
            # Director is process-wide: expose aggregate counts only, never
            # workspace, brand, integration, or external event identifiers.
            status=Health.UNKNOWN,
            metrics={
                "website_event_contract": "website.integration/v1",
                "event_count": len(events),
                "event_type_counts": event_type_counts,
                "latest_observed_at": latest["observed_at"] if latest else None,
                "director_signals": [],
                "director_signal_contract": "metrics.director_signals/v1",
            },
            issues=[],
            message=(
                "Website change notifications are recorded as evidence only; "
                "they do not authorize content generation or publication."
            ),
            timestamp=stamp,
        )

    async def handle_event(self, event_context: dict) -> dict:
        """Validate, deduplicate and persist one event before acknowledging it."""
        if not isinstance(event_context, dict):
            raise ValueError("event_context_must_be_an_object")
        event_name = str(event_context.get("event_name") or "")
        if event_name not in self.EVENT_NAMES:
            raise ValueError("unsupported_website_event")
        source = str(event_context.get("source") or "")
        is_dead_letter_replay = source == "dead_letter_replay"
        if source != self.SOURCE and not is_dead_letter_replay:
            raise ValueError("untrusted_website_event_source")

        workspace_id = str(event_context.get("workspace_id") or "").strip()
        brand_id = str(event_context.get("brand_id") or "").strip() or None
        event = event_context.get("payload")
        if (not workspace_id and not is_dead_letter_replay) or not isinstance(event, dict):
            raise ValueError("workspace_scope_and_event_payload_required")

        integration_id = str(event.get("integration_id") or "").strip()
        external_event_id = str(event.get("event_id") or "").strip()
        if (
            not integration_id
            or not external_event_id
            or not isinstance(event.get("payload"), dict)
        ):
            raise ValueError("invalid_website_event_envelope")

        event_type = event_name.removeprefix("website.integration.")
        if event_type not in {item.value for item in WebsiteEventType}:
            raise ValueError("event_type_not_in_website_contract")

        receipt = await asyncio.to_thread(
            self._load_verified_receipt,
            workspace_id,
            integration_id,
            external_event_id,
            event_type,
        )
        if receipt is None:
            raise ValueError("website_event_receipt_not_verified")
        if not workspace_id:
            workspace_id = str(receipt.get("workspace_id") or "").strip()
        if not workspace_id:
            raise ValueError("website_event_workspace_scope_not_resolved")

        digest = hashlib.sha256(
            f"{workspace_id}:{integration_id}:{external_event_id}".encode("utf-8")
        ).hexdigest()[:24]
        decision_id = f"website_event_{digest}"

        existing = await asyncio.to_thread(self._load_decision, decision_id)
        if existing and self._decision_is_complete(existing):
            self._remember_event(
                workspace_id, integration_id, external_event_id,
                event_type, receipt.get("occurred_at"), brand_id,
            )
            return {"accepted": True, "duplicate": True, "decision_id": decision_id}

        occurred_at = receipt.get("occurred_at")
        if isinstance(occurred_at, datetime):
            occurred_at = occurred_at.astimezone(timezone.utc).isoformat()
        elif not isinstance(occurred_at, str):
            occurred_at = datetime.now(timezone.utc).isoformat()

        observed_at = datetime.now(timezone.utc)
        observation = Observation(
            source=self.OBSERVER_NAME,
            sector=Sector.BRANDS,
            status=Health.UNKNOWN,
            metrics={
                "website_event_id": external_event_id,
                "integration_id": integration_id,
                "workspace_id": workspace_id,
                "event_type": event_type,
                "occurred_at": occurred_at,
                "brand_id": brand_id,
                "director_signals": [],
                "director_signal_contract": "metrics.director_signals/v1",
            },
            issues=[{
                "type": "website_change_notification",
                "event_type": event_type,
                "event_id": external_event_id,
                "requires_contextual_evaluation": True,
            }],
            message=(
                "A workspace-scoped website change was received. Retrieve current "
                "website context before deciding whether any work is warranted."
            ),
            timestamp=observed_at,
        )

        decision = Decision(
            id=decision_id,
            timestamp=observed_at,
            decision="Observe website change; defer action until context is evaluated",
            reason=(
                f"Received {event_type} from integration {integration_id}. "
                "A change notification is not a content-generation request; "
                "no task or publishing action is authorized by this event."
            ),
            confidence=0.0,
            sector=Sector.BRANDS,
            authority=Authority.NOTIFY,
            risk=Risk.LOW,
            task_id=None,
            outcome="observation_pending",
            stage="observation",
            observed_state=[{
                "source": self.OBSERVER_NAME,
                "workspace_id": workspace_id,
                "integration_id": integration_id,
                "event_id": external_event_id,
                "event_type": event_type,
                "occurred_at": occurred_at,
                "observed_at": observed_at.isoformat(),
            }],
            inferences=[{
                "claim": "Website event may indicate a change in business or content context.",
                "classification": "inference",
                "verification": "requires_current_context_retrieval",
            }],
            assumptions=[],
            evidence_sources=[{
                "source": "public.website_integration_events",
                "workspace_id": workspace_id,
                "integration_id": integration_id,
                "event_id": external_event_id,
                "event_type": event_type,
                "occurred_at": occurred_at,
                "verification": "durable_receipt_and_active_integration_checked",
            }],
            evidence_state="present",
            justification="Observe first; retrieve context before planning.",
            action_taken={
                "action": "observe_only",
                "task_created": False,
                "content_generation_started": False,
                "public_publishing_authorized": False,
            },
            confidence_basis={
                "value_source": "no_action_confidence",
                "calibration": "not_applicable",
                "evidence_source_count": 1,
                "fresh_evidence_source_count": 1,
            },
        )

        if not existing:
            await asyncio.to_thread(self._insert_decision_claim, decision, workspace_id)

        try:
            observation_exists = await asyncio.to_thread(
                self._observation_exists, workspace_id, external_event_id
            )
            if not observation_exists:
                await asyncio.to_thread(
                    self._persist_observation, observation, workspace_id
                )
        except Exception:
            logger.exception(
                "Director website event observation persistence failed event=%s",
                external_event_id,
            )
            raise

        decision.outcome = "observed"
        decision.action_taken["observation_persisted"] = True
        await asyncio.to_thread(self._upsert_decision, decision, workspace_id)

        self.director.decisions.append(decision)
        self._remember_event(
            workspace_id, integration_id, external_event_id,
            event_type, occurred_at, brand_id, observed_at,
        )

        logger.info(
            "Director recorded website event workspace=%s integration=%s type=%s",
            workspace_id,
            integration_id,
            event_type,
        )
        return {
            "accepted": True,
            "duplicate": False,
            "decision_id": decision_id,
            "observation_source": self.OBSERVER_NAME,
            "action": "observe_only",
        }

    def _hydrate_recent_events(self) -> None:
        """Restore bounded event metadata after restart without leaking tenant IDs."""
        try:
            response = (
                self.supabase.table("director_observations")
                .select("payload,timestamp")
                .contains("payload", {"source": self.OBSERVER_NAME})
                .order("timestamp", desc=True)
                .limit(self.MAX_RETAINED_EVENTS)
                .execute()
            )
            rows = list(response.data or [])
            for row in reversed(rows):
                payload = row.get("payload")
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except (TypeError, ValueError):
                        continue
                if not isinstance(payload, dict):
                    continue
                metrics = payload.get("metrics")
                if not isinstance(metrics, dict):
                    continue
                workspace_id = str(metrics.get("workspace_id") or "").strip()
                integration_id = str(metrics.get("integration_id") or "").strip()
                event_id = str(metrics.get("website_event_id") or "").strip()
                event_type = str(metrics.get("event_type") or "").strip()
                if not all((workspace_id, integration_id, event_id, event_type)):
                    continue
                observed_at = row.get("timestamp")
                if isinstance(observed_at, str):
                    try:
                        observed_at = datetime.fromisoformat(
                            observed_at.replace("Z", "+00:00")
                        )
                    except ValueError:
                        observed_at = None
                self._remember_event(
                    workspace_id,
                    integration_id,
                    event_id,
                    event_type,
                    metrics.get("occurred_at"),
                    str(metrics.get("brand_id") or "").strip() or None,
                    observed_at if isinstance(observed_at, datetime) else None,
                )
        except Exception:
            # Event ingestion remains available; missing history is observable.
            logger.warning(
                "Director website event history hydration failed",
                exc_info=True,
            )

    def _remember_event(
        self, workspace_id: str, integration_id: str, event_id: str,
        event_type: str, occurred_at: Any, brand_id: str | None,
        observed_at: datetime | None = None,
    ) -> None:
        if isinstance(occurred_at, datetime):
            occurred_at = occurred_at.astimezone(timezone.utc).isoformat()
        elif not isinstance(occurred_at, str):
            occurred_at = datetime.now(timezone.utc).isoformat()
        observed_at = observed_at or datetime.now(timezone.utc)
        event_key = f"{workspace_id}:{integration_id}:{event_id}"
        with self._latest_lock:
            self._latest_events[event_key] = {
                "workspace_id": workspace_id,
                "integration_id": integration_id,
                "event_id": event_id,
                "event_type": event_type,
                "occurred_at": occurred_at,
                "observed_at": observed_at.isoformat(),
                "brand_id": brand_id,
            }
            self._latest_events.move_to_end(event_key)
            while len(self._latest_events) > self.MAX_RETAINED_EVENTS:
                self._latest_events.popitem(last=False)

    def _load_verified_receipt(
        self, workspace_id: str, integration_id: str,
        event_id: str, event_type: str,
    ) -> dict | None:
        if not workspace_id:
            # UnifiedEventBus dead-letter replay retains the event payload and
            # event ID but intentionally omits original workspace metadata.
            # Recover scope only from one unambiguous durable authenticated receipt.
            replay_response = (
                self.supabase.table("website_integration_events")
                .select("workspace_id,event_id,event_type,occurred_at,payload,integration_id")
                .eq("integration_id", integration_id)
                .eq("event_id", event_id)
                .limit(2)
                .execute()
            )
            replay_rows = replay_response.data or []
            if len(replay_rows) != 1:
                return None
            workspace_id = str(replay_rows[0].get("workspace_id") or "").strip()
            if not workspace_id:
                return None
            replay_receipt = replay_rows[0]
        else:
            replay_receipt = None

        integration_response = (
            self.supabase.table("website_integrations")
            .select("integration_id,status,event_types")
            .eq("workspace_id", workspace_id)
            .eq("integration_id", integration_id)
            .limit(1)
            .execute()
        )
        integration_rows = integration_response.data or []
        integration = integration_rows[0] if integration_rows else None
        if not isinstance(integration, dict):
            return None
        if integration.get("status") not in {"configured", "connected"}:
            return None
        if event_type not in (integration.get("event_types") or []):
            return None

        if replay_receipt is not None:
            receipt = replay_receipt
        else:
            response = (
                self.supabase.table("website_integration_events")
                .select("event_id,event_type,occurred_at,payload,workspace_id")
                .eq("workspace_id", workspace_id)
                .eq("integration_id", integration_id)
                .eq("event_id", event_id)
                .limit(1)
                .execute()
            )
            rows = response.data or []
            receipt = rows[0] if rows else None
        if not isinstance(receipt, dict) or receipt.get("event_type") != event_type:
            return None
        return receipt

    @staticmethod
    def _decision_is_complete(record: dict) -> bool:
        payload = record.get("payload", record)
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (TypeError, ValueError):
                return False
        return isinstance(payload, dict) and payload.get("outcome") == "observed"

    def _load_decision(self, decision_id: str) -> dict | None:
        response = (
            self.supabase.table("director_decisions")
            .select("id,payload")
            .eq("id", decision_id)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        return rows[0] if rows else None

    def _insert_decision_claim(self, decision: Decision, workspace_id: str) -> None:
        record = self._decision_record(decision, workspace_id)
        self.supabase.table("director_decisions").insert(record).execute()

    def _upsert_decision(self, decision: Decision, workspace_id: str) -> None:
        record = self._decision_record(decision, workspace_id)
        self.supabase.table("director_decisions").upsert(
            record, on_conflict="id"
        ).execute()

    @staticmethod
    def _decision_record(decision: Decision, workspace_id: str) -> dict:
        payload = safe({**asdict(decision), "workspace_id": workspace_id})
        return {
            "id": decision.id,
            # director_decisions.payload is JSONB; store an object, not a
            # JSON-encoded string scalar, so JSONB filters and readers work.
            "payload": payload,
            "timestamp": iso(decision.timestamp),
            "workspace_id": workspace_id,
        }

    def _observation_exists(self, workspace_id: str, event_id: str) -> bool:
        response = (
            self.supabase.table("director_observations")
            .select("id")
            .eq("workspace_id", workspace_id)
            .contains("payload", {"metrics": {"website_event_id": event_id}})
            .limit(1)
            .execute()
        )
        return bool(response.data)

    def _persist_observation(self, observation: Observation, workspace_id: str) -> None:
        record = {
            # director_observations.payload is JSONB and is queried by event ID.
            "payload": safe(asdict(observation)),
            "timestamp": iso(observation.timestamp),
            "workspace_id": workspace_id,
        }
        self.supabase.table("director_observations").insert(record).execute()
