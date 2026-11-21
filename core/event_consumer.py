"""
Event Consumer Service — The Reactive Brain.

Listens to ALL events and triggers appropriate actions:
- backup_completed(failed) → Director recovery task
- engine_alert(critical) → Escalation
- trend_detected → ConditionEngine analysis
- task_failed(3x) → Auto-incident
- post_published → Engagement tracking
- content_failed(3x) → Investigation

This is what makes the system SELF-HEALING.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import asyncio
import inspect
import uuid
import hashlib
from collections import defaultdict, deque
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("kanyoza.event_consumer")


class EventConsumer:
    """Central event consumer — reacts to events autonomously."""

    # ═══════════════════════════════════════════════════════════════
    # THRESHOLDS
    # ═══════════════════════════════════════════════════════════════

    FAILURE_THRESHOLD = 3           # After 3 failures, escalate
    CRITICAL_ALERT_COOLDOWN = 1800  # 30 min between critical alerts
    EVENT_FLOOD_THRESHOLD = 50      # Max events per minute per service
    CORRELATION_WINDOW = 300        # 5 min window for correlation

    def __init__(self, shared_bus=None, supabase_client=None):
        self.shared_bus = shared_bus
        self.supabase = supabase_client

        self._running = False
        self._thread = None

        # Failure tracking
        self._failure_counts: Dict[str, int] = defaultdict(int)
        self._last_alert_time: Dict[str, float] = {}

        # Event history for correlation
        self._recent_events: deque = deque(maxlen=500)
        self._event_counts: Dict[str, int] = defaultdict(int)
        self._event_window_start = time.monotonic()

        # Correlation rules
        self._correlations = []
        self._consumer_id = f"event-consumer:{uuid.uuid4().hex}"

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._consume_loop,
            name="event-consumer",
            daemon=True,
        )
        self._thread.start()
        logger.info("🔌 Event Consumer started — system is now reactive")

    def stop(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    def _consume_loop(self) -> None:
        while self._running:
            try:
                self._poll_events()
                self._check_correlations()
                self._check_rate_limits()
            except Exception:
                logger.exception("Event Consumer error")
            time.sleep(10)

    def _poll_events(self) -> None:
        """Poll for new events and react."""
        if not self.supabase:
            return

        try:
            # SharedEventBus v2 writes to the canonical `events` table and
            # uses `event_type`; the legacy consumer used the removed
            # `shared_event_bus`/`event_name` contract and therefore silently
            # missed every cross-process event.
            cutoff = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
            result = self.supabase.table("events") \
                .select("*") \
                .eq("processed", False) \
                .or_(f"claimed_at.is.null,claimed_at.lt.{cutoff}") \
                .order("created_at") \
                .limit(100) \
                .execute()

            for event in result.data or []:
                event_id = event.get("id")
                if not event_id or not self._claim_event(event_id):
                    continue
                normalized = dict(event)
                normalized.setdefault("event_name", normalized.get("event_type", ""))
                normalized.setdefault("source_service", normalized.get("source", "unknown"))
                self._process_event(normalized)

        except Exception as exc:
            logger.error("Event polling failed: %s", exc)

    def _process_event(self, event: Dict[str, Any]) -> None:
        """Process a single event and trigger appropriate reaction."""
        event_name = event.get("event_name", "")
        event_id = event.get("id")
        source = event.get("source_service", "unknown")

        try:
            payload = json.loads(event.get("payload", "{}"))
        except json.JSONDecodeError:
            payload = {}

        # Track for correlation
        self._recent_events.append({
            "event_name": event_name,
            "source": source,
            "payload": payload,
            "timestamp": datetime.now(timezone.utc),
        })

        # ═══════════════════════════════════════════════════════
        # REACT TO EVENTS
        # ═══════════════════════════════════════════════════════

        if event_name == "backup_completed" and payload.get("status") == "failed":
            self._handle_backup_failure(payload)

        elif event_name == "engine_alert" and payload.get("status") == "critical":
            self._handle_critical_alert(payload)

        elif event_name == "trend_detected":
            self._handle_trend(payload)

        elif event_name == "task_failed":
            self._handle_task_failure(payload)

        elif event_name == "content_failed":
            self._handle_content_failure(payload)

        elif event_name == "post_published":
            self._handle_post_published(payload)

        elif event_name == "service_heartbeat":
            self._handle_heartbeat(payload)

        elif event_name == "heal_command":
            if not self._handle_heal_command(payload):
                logger.warning("heal_command retained for retry event_id=%s", event_id)
                self._release_event_claim(event_id)
                return

        elif event_name == "director.automation_job_recovery.requested":
            if not self._handle_director_job_recovery_request(payload):
                logger.warning("Director job recovery request retained for retry event_id=%s", event_id)
                self._release_event_claim(event_id)
                return

        # Mark as processed only after the handler has accepted the event.
        self._mark_processed(event_id)

    def _release_event_claim(self, event_id: Any) -> None:
        """Release a failed handler claim so the durable event can be retried."""
        try:
            self.supabase.table("events").update({
                "claimed_by": None,
                "claimed_at": None,
            }).eq("id", event_id).eq("claimed_by", self._consumer_id).execute()
        except Exception:
            logger.exception("Failed to release event claim id=%s", event_id)

    def _handle_director_job_recovery_request(self, payload: Dict[str, Any]) -> bool:
        """Idempotently admit an advisory platform recovery mission.

        This stage never retries/quarantines jobs and never replays publication.
        Admitted diagnosis tasks remain approval_required; Director may run only
        their separate read-only advisory specialists, never task execution actions.
        """
        if (
            payload.get("trigger") != "automation_job_failure_threshold"
            or payload.get("recovery_mode") != "bounded_advisory_sweep"
            or payload.get("automatic_retry") is not False
            or payload.get("publication_replay_allowed") is not False
        ):
            logger.error("Rejecting malformed or unsafe Director recovery request")
            return True

        correlation_id = str(payload.get("correlation_id") or "").strip()
        failed_jobs = payload.get("failed_jobs")
        if not correlation_id or isinstance(failed_jobs, bool) or not isinstance(failed_jobs, int) or failed_jobs < 9:
            logger.error("Rejecting Director recovery request with invalid identity/threshold")
            return True

        intelligence = payload.get("failure_intelligence")
        if not isinstance(intelligence, dict) or intelligence.get("status") != "available":
            logger.warning("Director recovery admission deferred: failure intelligence unavailable")
            return False
        categories = intelligence.get("categories")
        if not isinstance(categories, dict) or not categories:
            logger.info("Director recovery request accepted with no classified failed jobs")
            return True

        try:
            allowed_categories = {
                "caption_quality", "voice_compliance", "evidence_grounding",
                "semantic_validation", "gateway_caller_policy",
                "provider_availability", "unclassified",
            }
            ranked = sorted(
                (
                    (name, item)
                    for name, item in categories.items()
                    if (
                        isinstance(name, str)
                        and name in allowed_categories
                        and isinstance(item, dict)
                        and isinstance(item.get("count"), int)
                        and not isinstance(item.get("count"), bool)
                        and item["count"] > 0
                    )
                ),
                key=lambda pair: (-pair[1]["count"], pair[0]),
            )[:8]
            if not ranked:
                return True

            namespace = uuid.UUID("c7c2e8c2-0b8a-4c17-8a10-40ef8c8fcb13")
            mission_id = str(uuid.uuid5(namespace, f"automation-job-recovery:{correlation_id}"))
            idempotency_key = f"job-recovery:{correlation_id}"[:160]
            tasks = []
            for category, details in ranked:
                task_id = str(uuid.uuid5(namespace, f"{mission_id}:{category}"))
                title = f"Diagnose failed automation jobs: {category}"[:180]
                task_payload = {
                    "id": task_id,
                    "mission_id": mission_id,
                    "scope_type": "platform",
                    "workspace_id": None,
                    "status": "approval_required",
                    "title": title,
                    "description": (
                        "Read-only diagnosis of failed automation jobs in this failure category. "
                        "Return evidence and recommendations only; do not retry, quarantine, publish, "
                        "or replay any job."
                    ),
                    "task_type": "automation_job_failure_diagnosis",
                    "failure_category": category[:120],
                    "failed_job_count": min(int(details.get("count") or 0), 500),
                    "job_ids": [
                        str(value)[:128]
                        for value in (details.get("job_ids") or [])[:20]
                        if isinstance(value, (str, int)) and str(value).strip()
                    ],
                    "recovery_proposals": [
                        item for item in (details.get("recovery_proposals") or [])[:20]
                        if isinstance(item, dict)
                    ],
                    "automatic_retry": False,
                    "publication_replay_allowed": False,
                }
                tasks.append({
                    "id": task_id,
                    "workspace_id": None,
                    "scope_type": "platform",
                    "status": "approval_required",
                    "payload": task_payload,
                })

            canonical = json.dumps({
                "mission_id": mission_id,
                "correlation_id": correlation_id,
                "tasks": tasks,
                "automatic_retry": False,
                "publication_replay_allowed": False,
            }, sort_keys=True, separators=(",", ":"))
            fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

            async def admit_and_dispatch():
                from services.director import get_director
                from services.director.orchestration.store import MissionRuntimeStore
                from services.director.orchestration.runtime import MissionOrchestrationRuntime
                from services.director.orchestration.contracts import AdapterRegistry
                from services.director.orchestration.swarm_planning import SwarmCandidate
                from services.director.orchestration.automation_job_diagnostics_adapter import (
                    AutomationJobDiagnosticsMissionAdapter,
                )
                from services.job_stats_service import JobStatsService

                director = await get_director()
                if director is None or getattr(director, "store", None) is None:
                    raise RuntimeError("director_runtime_unavailable")
                runtime_store = MissionRuntimeStore(director.store)
                admitted = await runtime_store.admit_platform_recovery_mission(
                    idempotency_key=idempotency_key,
                    mission_id=mission_id,
                    request_fingerprint=fingerprint,
                    correlation_id=correlation_id,
                    tasks=tasks,
                )
                if not isinstance(admitted, dict) or admitted.get("error"):
                    return admitted

                # Platform-only runtime: no synthetic workspace and no
                # workspace-bound adapters.
                supabase_client = getattr(director.store, "supabase", None)
                if supabase_client is None:
                    raise RuntimeError("platform_recovery_supabase_client_unavailable")
                registry = AdapterRegistry()
                registry.register(AutomationJobDiagnosticsMissionAdapter(
                    job_stats_service=JobStatsService(supabase_client=supabase_client)
                ))
                runtime = MissionOrchestrationRuntime(store=runtime_store, adapters=registry)
                current = await runtime.load_snapshot(mission_id=mission_id, workspace_id=None)
                existing_agents = dict(current.state_snapshot.get("agents") or {})
                candidates = []
                task_inputs = {}
                for index, task in enumerate(tasks):
                    payload_data = task.get("payload") or {}
                    task_id = str(task.get("id") or "")
                    identity = hashlib.sha256(
                        f"{correlation_id}|{task_id}".encode("utf-8")
                    ).hexdigest()
                    agent_id = f"jobdiag-{identity[:20]}"
                    task_inputs[task_id] = {
                        "failure_category": payload_data.get("failure_category"),
                        "job_ids": payload_data.get("job_ids") or [],
                    }
                    if agent_id in existing_agents:
                        continue
                    candidates.append(SwarmCandidate(
                        agent_id=agent_id,
                        task_id=task_id,
                        engine_id="automation_job_diagnostics",
                        capability="automation.jobs.diagnose",
                        role="read-only-failure-diagnostician",
                        priority=max(0, 100 - index),
                        estimated_tokens=300,
                    ))

                # Execute any previously admitted-but-not-started specialist
                # on event redelivery, without creating duplicate agent records.
                prefix_digest = hashlib.sha256(correlation_id.encode("utf-8")).hexdigest()[:16]

                async def execute_spawned(snapshot, wave_index):
                    agents = dict(snapshot.state_snapshot.get("agents") or {})
                    for agent_id, agent in agents.items():
                        task_id = str((agent or {}).get("task_id") or "")
                        if (agent or {}).get("status") != "spawned" or task_id not in task_inputs:
                            continue
                        try:
                            await runtime.execute_agent(
                                mission_id=mission_id,
                                workspace_id=None,
                                agent_id=agent_id,
                                objective=(
                                    "Refresh read-only evidence for this automation-job failure category. "
                                    "Identify current failed job IDs, likely common causes, and safe next "
                                    "checks. Do not retry, quarantine, publish, or replay any job."
                                ),
                                inputs=task_inputs[task_id],
                                idempotency_prefix=f"jr-{prefix_digest}-{wave_index}-{agent_id[-8:]}",
                                correlation_id=correlation_id,
                                timeout_seconds=20,
                            )
                        except Exception:
                            logger.exception(
                                "director_platform_recovery_specialist_failed mission_id=%s agent_id=%s",
                                mission_id, agent_id,
                            )

                await execute_spawned(current, 0)
                wave = 0
                while candidates and wave < 2:
                    batch, candidates = candidates[:4], candidates[4:]
                    await runtime.spawn_swarm(
                        mission_id=mission_id,
                        workspace_id=None,
                        task_ids=[item.task_id for item in batch],
                        candidates=batch,
                        complexity_score=min(1.0, max(0.125, len(batch) / 8)),
                        idempotency_prefix=f"jr-{prefix_digest}-{wave}",
                        correlation_id=correlation_id,
                    )
                    snapshot = await runtime.load_snapshot(mission_id=mission_id, workspace_id=None)
                    await execute_spawned(snapshot, wave + 1)
                    wave += 1

                logger.warning(
                    "director_platform_recovery_specialists_dispatched mission_id=%s admitted_tasks=%d spawned_candidates=%d correlation_id=%s",
                    mission_id, len(tasks), min(8, len(tasks)), correlation_id,
                )
                return admitted

            result = asyncio.run(admit_and_dispatch())
            if not isinstance(result, dict) or result.get("error"):
                logger.error("Platform recovery mission admission/dispatch rejected: %s", result)
                return False
            logger.warning(
                "director_platform_recovery_mission_admitted mission_id=%s tasks=%d replayed=%s correlation_id=%s",
                result.get("mission_id"), len(result.get("task_ids") or []),
                bool(result.get("replayed")), correlation_id,
            )
            return True
        except Exception:
            logger.exception("Director platform recovery mission admission failed")
            return False

    def _handle_backup_failure(self, payload: Dict[str, Any]) -> None:
        """Backup failed — track and escalate if repeated."""
        key = "backup_failure"
        self._failure_counts[key] += 1
        count = self._failure_counts[key]

        logger.warning(f"💾 Backup failure #{count}")

        if count >= self.FAILURE_THRESHOLD:
            # Escalate to Director
            self.shared_bus.publish("engine_alert", {
                "engine": "backup",
                "status": "critical",
                "message": f"Backup failed {count} times consecutively",
            })
            self._failure_counts[key] = 0  # Reset after escalation

    def _handle_critical_alert(self, payload: Dict[str, Any]) -> None:
        """Critical alert — apply cooldown and escalate."""
        engine = payload.get("engine", "unknown")

        # Cooldown check
        now = time.monotonic()
        last_alert = self._last_alert_time.get(engine, 0)

        if now - last_alert < self.CRITICAL_ALERT_COOLDOWN:
            logger.debug(f"Alert cooldown active for {engine} — skipping")
            return

        self._last_alert_time[engine] = now
        logger.critical(f"🚨 CRITICAL: {engine} — {payload.get('message', '')}")

        # Escalate via Telegram
        try:
            from services.email_service import email_service
            email_service.send_telegram(
                f"🚨 <b>CRITICAL ALERT</b>\n"
                f"Engine: {engine}\n"
                f"Message: {payload.get('message', '')}\n"
                f"<i>{datetime.now(timezone.utc).strftime('%H:%M UTC')}</i>"
            )
        except Exception:
            pass

    def _handle_trend(self, payload: Dict[str, Any]) -> None:
        """Trend detected — feed to ConditionEngine."""
        topic = payload.get("topic", "")
        logger.info(f"📈 Trend: {topic}")

        # Publish for ConditionEngine to analyze
        self.shared_bus.publish("trend_analysis_request", {
            "topic": topic,
            "source": payload.get("source", "unknown"),
        })

    def _handle_task_failure(self, payload: Dict[str, Any]) -> None:
        """Task failed — track and escalate."""
        task_desc = payload.get("description", "unknown task")
        key = f"task:{task_desc}"
        self._failure_counts[key] += 1
        count = self._failure_counts[key]

        logger.warning(f"❌ Task failure #{count}: {task_desc}")

        if count >= self.FAILURE_THRESHOLD:
            self.shared_bus.publish("engine_alert", {
                "engine": "director",
                "status": "degraded",
                "message": f"Task '{task_desc}' failed {count} times",
            })
            self._failure_counts[key] = 0

    def _handle_content_failure(self, payload: Dict[str, Any]) -> None:
        """Content generation failed — track and escalate."""
        brand = payload.get("brand", "unknown")
        key = f"content:{brand}"
        self._failure_counts[key] += 1
        count = self._failure_counts[key]

        logger.warning(f"📝 Content failure #{count} for {brand}")

        if count >= self.FAILURE_THRESHOLD:
            self.shared_bus.publish("engine_alert", {
                "engine": "content_engine",
                "status": "degraded",
                "message": f"Content generation failed {count} times for {brand}",
            })
            self._failure_counts[key] = 0

    def _handle_post_published(self, payload: Dict[str, Any]) -> None:
        """Post published — track for engagement."""
        topic = payload.get("topic", "")
        brand = payload.get("brand", "")
        logger.info(f"✅ Post published: {topic} for {brand}")

        # Publish for engagement tracking
        self.shared_bus.publish("engagement_tracking_request", {
            "brand": brand,
            "topic": topic,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def _handle_heal_command(self, payload: Dict[str, Any]) -> bool:
        """Execute only governed, explicitly supported recovery commands.

        AutonomousLoop emits a heal_command as a request/fact.  This consumer
        is the bridge into GuardianRecovery; it never performs an ungoverned
        restart directly.
        """
        action = payload.get("action") or payload.get("recommended_action")
        target = payload.get("engine") or payload.get("service") or payload.get("target")
        workspace_id = payload.get("workspace_id") or "platform"
        correlation_id = payload.get("correlation_id") or f"heal:{action}:{target}"
        if not action or not target:
            logger.warning("Ignoring malformed heal_command: action/target required")
            return True
        try:
            from services.recovery_guardian import GuardianRecovery, build_internal_recovery_executor
            recovery = GuardianRecovery()
            executor = build_internal_recovery_executor()

            async def run() -> dict:
                async def verify(params: dict) -> bool:
                    try:
                        from services import meta_engine as module
                        meta = getattr(module, "meta_engine", None)
                        if meta is None:
                            return False
                        check = getattr(meta, "check_engine_health", None)
                        if not callable(check):
                            return False
                        result = check(params["target"], force=True)
                        if inspect.isawaitable(result):
                            result = await result
                        status = getattr(result, "status", result.get("status") if isinstance(result, dict) else None)
                        value = getattr(status, "value", status)
                        return str(value).lower() in {"healthy", "nominal", "ok", "alive"}
                    except Exception as exc:
                        logger.warning("Recovery verification failed for %s: %s", target, exc)
                        return False

                return await recovery.recover(
                    workspace_id=workspace_id,
                    action=action,
                    target=target,
                    reason=payload.get("reason", "autonomous recovery request"),
                    correlation_id=correlation_id,
                    executor=executor.execute,
                    verifier=verify,
                    blast_radius=int(payload.get("blast_radius") or 1),
                    critical=bool(payload.get("critical", False)),
                )

            result = asyncio.run(run())
            logger.info("Governed recovery result action=%s target=%s status=%s", action, target, result.get("status"))
            return result.get("status") not in {"failed", "verification_failed", "not_recovered"}
        except Exception as exc:
            logger.exception("Governed recovery dispatch failed: %s", exc)
            return False

    def _handle_heartbeat(self, payload: Dict[str, Any]) -> None:
        """Service heartbeat — track uptime."""
        service = payload.get("service", "")
        logger.debug(f"💓 Heartbeat: {service}")

    def _check_correlations(self) -> None:
        """Correlate recent events to find patterns."""
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=self.CORRELATION_WINDOW)

        # Filter recent events
        recent = [
            e for e in self._recent_events
            if e["timestamp"] >= cutoff
        ]

        if not recent:
            return

        # Check: Multiple engine_alerts in short time
        critical_alerts = [
            e for e in recent
            if e["event_name"] == "engine_alert" and e["payload"].get("status") == "critical"
        ]

        if len(critical_alerts) >= 3:
            engines = [e["payload"].get("engine", "") for e in critical_alerts]
            logger.critical(f"🚨 CORRELATION: Multiple critical alerts: {engines}")
            self.shared_bus.publish("engine_alert", {
                "engine": "system",
                "status": "critical",
                "message": f"{len(critical_alerts)} critical alerts in {self.CORRELATION_WINDOW}s",
            })

    def _check_rate_limits(self) -> None:
        """Check for event flooding."""
        now = time.monotonic()
        window_elapsed = now - self._event_window_start

        if window_elapsed >= 60:
            # Reset window
            flooded_services = [
                service for service, count in self._event_counts.items()
                if count > self.EVENT_FLOOD_THRESHOLD
            ]

            for service in flooded_services:
                logger.warning(f"🌊 Event flood detected from {service}: {self._event_counts[service]} events/min")
                self.shared_bus.publish("engine_alert", {
                    "engine": service,
                    "status": "degraded",
                    "message": f"Event flood: {self._event_counts[service]} events in 60s",
                })

            self._event_counts.clear()
            self._event_window_start = now

    def _claim_event(self, event_id: Any) -> bool:
        """Atomically claim an event so multiple consumers do not execute it twice."""
        try:
            result = self.supabase.table("events").update({
                "claimed_by": self._consumer_id,
                "claimed_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", event_id).eq("processed", False).or_(
                "claimed_at.is.null"
            ).select("id").execute()
            return bool(result.data)
        except Exception as exc:
            logger.warning("Event claim failed id=%s: %s", event_id, exc)
            return False

    def _mark_processed(self, event_id: Any) -> None:
        """Mark the canonical events-table record as processed."""
        try:
            self.supabase.table("events") \
                .update({"processed": True, "processed_at": datetime.now(timezone.utc).isoformat(), "claimed_by": self._consumer_id}) \
                .eq("id", event_id) \
                .eq("claimed_by", self._consumer_id) \
                .execute()
        except Exception as exc:
            logger.warning("Event acknowledgement failed id=%s: %s", event_id, exc)

    def get_status(self) -> Dict[str, Any]:
        return {
            "running": self._running,
            "failure_counts": dict(self._failure_counts),
            "events_in_window": len(self._recent_events),
            "event_rate": sum(self._event_counts.values()),
        }


# ═══════════════════════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════════════════════

_event_consumer: Optional[EventConsumer] = None


def init_event_consumer(shared_bus=None, supabase_client=None) -> EventConsumer:
    global _event_consumer
    _event_consumer = EventConsumer(
        shared_bus=shared_bus,
        supabase_client=supabase_client,
    )
    _event_consumer.start()
    return _event_consumer


def get_event_consumer() -> Optional[EventConsumer]:
    return _event_consumer


__all__ = ["EventConsumer", "init_event_consumer", "get_event_consumer"]
