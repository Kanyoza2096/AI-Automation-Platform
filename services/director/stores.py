"""
KANYOZA DIRECTOR — Storage Layer
=================================

Two store implementations behind a common shape:

  AsyncSupabaseStore — durable, PostgreSQL-backed. Used in production.
  InMemoryStore      — bounded, process-local. Used in dev / tests / when
                       no Supabase client is available.

Both expose the same method surface so the Director can swap them
transparently. The AsyncSupabaseStore wraps every query in a retry loop
with a circuit breaker; transport failures trigger a pooled client
re-acquisition so a dead HTTP connection does not poison subsequent calls.

Dependency order: utils → models → stores. This module must not import
from core or api.

v5.4 CHANGES:
  - _refresh_client() no longer calls create_fresh_service_client().
    That factory built a brand-new Supabase client on every transport
    failure, and on supabase-py <2.6 the construction went through the
    ClientOptions(httpx_client=...) fallback path mid-flight — producing
    a GOAWAY cascade every 30 minutes and failing every
    director_observations insert (bug report 2026-10-26, Director
    refresh loop). The new path re-acquires the process-wide singleton
    via get_service_client_from_settings(), which was built at boot with
    an HTTP/1.1 pooled transport. No new httpx.Client is constructed,
    no ClientOptions re-parse, no fresh-pool exhaustion mid-flight.

v5.3 CHANGES:
  - Both stores gain list_learnings_since(since, limit). Used by
    Director._replay_learning_history at startup to reconstruct
    LearningLoop state from the durable log.
  - save_learning now accepts the full LearningSignal dict shape
    (capability, sector, outcome, duration_ms, error_type, context,
    timestamp). The context field carries target and error_class
    — no schema change needed; the payload is JSON.
  - InMemoryStore.list_learnings_since filters by timestamp when present.

No behavior change until the Director is updated to call the new method.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import hashlib
from collections import deque
from dataclasses import asdict
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from .utils import safe, iso, iso_now
from .models import (
    AsyncCircuitBreaker,
    Decision,
    Observation,
    SupabaseErrorClassifier,
    Task,
)

log = logging.getLogger("kanyoza.director")


# ═══════════════════════════════════════════════════════════════════════════
# ASYNC SUPABASE STORE
# ═══════════════════════════════════════════════════════════════════════════

class AsyncSupabaseStore:
    """PostgreSQL-backed store via Supabase (async)."""

    def __init__(self, supabase_client):
        self.supabase = supabase_client
        self._client_factory: Optional[Callable[[], Any]] = None
        self._refresh_lock = asyncio.Lock()
        try:
            from services.supabase import get_service_client_from_settings
            self._client_factory = get_service_client_from_settings
        except Exception as exc:
            log.warning(
                "Director DB: pooled Supabase client factory unavailable: %s",
                str(exc)[:200],
            )
        self.circuit_breaker = AsyncCircuitBreaker(name="supabase_store")

    @staticmethod
    def _is_transport_failure(exc: Exception) -> bool:
        """Identify failures where reusing the HTTP transport is unsafe."""
        names = {
            "ConnectError", "ConnectTimeout", "ReadError", "ReadTimeout",
            "WriteError", "WriteTimeout", "PoolTimeout", "RemoteProtocolError",
            "LocalProtocolError", "NetworkError", "ConnectionError",
            "BrokenPipeError", "ConnectionResetError", "ConnectionAbortedError",
        }
        if exc.__class__.__name__ in names:
            return True
        text = str(exc).lower()
        return any(token in text for token in (
            "broken pipe", "connectionterminated", "connection terminated",
            "remoteprotocolerror", "connection reset", "connection aborted",
            "connection refused", "connection pool", "transport",
        ))

    async def _refresh_client(self) -> bool:
        """Re-acquire the pooled Supabase client after a transport failure.

        The client is a process-wide singleton built at boot with an
        HTTP/1.1 pooled transport. Calling the factory returns the same
        instance — no new httpx.Client is constructed, no ClientOptions
        re-parse, no fresh-pool exhaustion mid-flight. If the singleton
        itself is broken (None), this is a no-op and the retry loop
        degrades to the circuit breaker.

        History: this method previously called
        create_fresh_service_client(), which built a brand-new client on
        every transport failure. That path hit the supabase-py <2.6
        ClientOptions fallback mid-flight and produced a GOAWAY cascade
        every 30 minutes (bug report 2026-10-26, Director refresh loop).
        """
        if not self._client_factory:
            return False
        async with self._refresh_lock:
            try:
                client = await asyncio.to_thread(self._client_factory)
                if client is not None:
                    self.supabase = client
                    log.info(
                        "Director DB: re-acquired pooled Supabase client "
                        "after transport failure"
                    )
                    return True
            except Exception as exc:
                log.warning(
                    "Director DB: Supabase client re-acquisition failed: %s",
                    str(exc)[:200],
                )
        return False

    async def _execute_with_retry(
        self,
        operation_name: str,
        fn: Callable,
        max_retries: int = 2,
    ) -> Optional[Any]:
        if self.circuit_breaker.is_open:
            return None

        last_error = None
        for attempt in range(max_retries + 1):
            try:
                current_fn = fn
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(None, current_fn)
                await self.circuit_breaker._record_success()
                return result
            except Exception as exc:
                last_error = exc
                status = SupabaseErrorClassifier._extract_status(exc)

                if SupabaseErrorClassifier.is_permanent(exc):
                    await self.circuit_breaker._record_failure()
                    log.error(
                        "Director DB: '%s' FAILED (HTTP %s, permanent): %s",
                        operation_name, status, SupabaseErrorClassifier.describe(exc),
                    )
                    return None

                if attempt >= max_retries:
                    await self.circuit_breaker._record_failure()
                    log.error(
                        "Director DB: '%s' FAILED after %d retries (HTTP %s): %s",
                        operation_name, max_retries, status,
                        SupabaseErrorClassifier.describe(exc),
                    )
                    return None

                if self._is_transport_failure(exc):
                    await self._refresh_client()

                delay = min(1.0 * (2 ** attempt), 10.0)
                delay *= random.uniform(0.75, 1.25)
                log.warning(
                    "Director DB: '%s' attempt %d/%d failed (%s). Retrying in %.1fs...",
                    operation_name, attempt + 1, max_retries,
                    "transport" if self._is_transport_failure(exc) else "retryable",
                    delay,
                )
                await asyncio.sleep(delay)
        return None

    async def save_task(self, task: Task) -> bool:
        """Persist a task and report whether the write was acknowledged."""
        workspace_id = str(task.workspace_id).strip() if task.workspace_id else None
        scope_type = "workspace" if workspace_id else task.scope_type
        if scope_type not in {"platform", "workspace", "legacy_unclassified"}:
            log.error("Director DB: refusing task with invalid ownership scope")
            return False
        if scope_type == "workspace" and not workspace_id:
            log.error("Director DB: refusing workspace task without workspace_id")
            return False
        if scope_type != "workspace" and workspace_id:
            log.error("Director DB: refusing non-workspace task with workspace_id")
            return False
        result = await self._execute_with_retry(
            "insert into director_tasks",
            lambda: self.supabase.table("director_tasks").upsert({
                "id": task.id,
                "workspace_id": workspace_id,
                "scope_type": scope_type,
                "payload": safe(asdict(task)),
                "created_at": iso(task.created_at),
                "status": task.status.value,
            }).execute(),
        )
        return result is not None

    async def stage_mission_admission(
        self, *, workspace_id: str, idempotency_key: str, plan_id: str,
        mission_id: str, request_fingerprint: str, tasks: list[dict],
    ) -> Optional[dict]:
        """Atomically persist a staged mission and its idempotency receipt."""
        result = await self._execute_with_retry(
            "stage Director mission admission",
            lambda: self.supabase.rpc("stage_director_mission", {
                "p_workspace_id": workspace_id,
                "p_idempotency_key": idempotency_key,
                "p_plan_id": plan_id,
                "p_mission_id": mission_id,
                "p_request_fingerprint": request_fingerprint,
                "p_tasks": tasks,
            }).execute(),
        )
        if result is None:
            return None
        payload = result.data
        if isinstance(payload, list) and payload:
            payload = payload[0]
        return payload if isinstance(payload, dict) else None

    async def brand_belongs_to_workspace(self, brand_id: str, workspace_id: str) -> bool:
        """Confirm a brand target is owned by the requesting workspace."""
        result = await self._execute_with_retry(
            "verify Director mission brand ownership",
            lambda: self.supabase.table("brands")
            .select("id").eq("id", brand_id)
            .eq("workspace_id", workspace_id).limit(1).execute(),
        )
        return bool(result and result.data)

    async def get_task(self, task_id: str) -> Optional[dict]:
        result = await self._execute_with_retry(
            "get from director_tasks",
            lambda: self.supabase.table("director_tasks")
            .select("*").eq("id", task_id).limit(1).execute(),
        )
        return dict(result.data[0]) if result and result.data else None

    async def list_tasks(
        self, limit: int = 200, *, workspace_id: Optional[str] = None,
        scope_type: str = "platform",
    ) -> List[dict]:
        """List one ownership scope only; never mix tenant and platform tasks."""
        if scope_type not in {"platform", "workspace", "legacy_unclassified"}:
            raise ValueError("invalid Director task scope")
        if scope_type == "workspace" and not (workspace_id and workspace_id.strip()):
            raise ValueError("workspace_id is required for workspace task reads")
        query = self.supabase.table("director_tasks").select("*")
        query = query.eq("scope_type", scope_type)
        if scope_type == "workspace":
            query = query.eq("workspace_id", workspace_id.strip())
        elif workspace_id is not None:
            raise ValueError("workspace_id cannot be combined with a non-workspace scope")
        result = await self._execute_with_retry(
            "list Director tasks by ownership scope",
            lambda: query.order("created_at", desc=True).limit(limit).execute(),
        )
        return [dict(row) for row in (result.data or [])] if result else []

    async def list_approved_workspace_tasks(self, limit: int = 5000) -> List[dict]:
        """Internal recovery scan; never exposed to workspace-facing API callers."""
        bounded_limit = max(1, min(int(limit), 5000))
        result = await self._execute_with_retry(
            "list approved workspace tasks for Director journal reconciliation",
            lambda: self.supabase.table("director_tasks").select("*")
            .eq("scope_type", "workspace")
            .eq("status", Status.APPROVED.value)
            .order("created_at", desc=True)
            .limit(bounded_limit)
            .execute(),
        )
        return [dict(row) for row in (result.data or [])] if result else []

    MAX_PERSISTED_OBSERVATIONS = 500
    MAX_PERSISTED_DECISIONS = 250
    MAX_PERSISTED_LEARNINGS = 500

    async def _prune_recent_records(
        self, table_name: str, max_rows: int, *,
        scope_type: Optional[str] = None, workspace_id: Optional[str] = None,
    ) -> None:
        """Retain bounded history without pruning another workspace's observations."""
        def excess_query():
            query = self.supabase.table(table_name).select("id")
            if scope_type is not None:
                query = query.eq("scope_type", scope_type)
            if workspace_id is not None:
                query = query.eq("workspace_id", workspace_id)
            return query.order("timestamp", desc=True).range(max_rows, max_rows + 999).execute()

        rows = await self._execute_with_retry(
            f"select excess rows from {table_name}",
            excess_query,
        )
        excess_ids = [row.get("id") for row in (rows.data or []) if row.get("id")] if rows else []
        if excess_ids:
            await self._execute_with_retry(
                f"prune excess rows from {table_name}",
                lambda: self.supabase.table(table_name).delete().in_("id", excess_ids).execute(),
            )

    async def save_decision(self, decision: Decision) -> None:
        result = await self._execute_with_retry(
            "insert into director_decisions",
            lambda: self.supabase.table("director_decisions").upsert({
                "id": decision.id,
                "payload": safe(asdict(decision)),
                "timestamp": iso(decision.timestamp),
            }).execute(),
        )
        if result is not None:
            await self._prune_recent_records("director_decisions", self.MAX_PERSISTED_DECISIONS)

    async def list_decisions(self, limit: int = 100) -> List[dict]:
        result = await self._execute_with_retry(
            "list from director_decisions",
            lambda: self.supabase.table("director_decisions")
            .select("*").order("timestamp", desc=True).limit(limit).execute(),
        )
        return [dict(row) for row in (result.data or [])] if result else []

    async def save_strategic_plan(self, plan: dict) -> bool:
        """Persist and verify a validated advisory strategic plan.

        Supabase JSONB columns must receive a JSON-compatible Python value.
        Passing json.dumps(...) stores a JSON string scalar, not a JSON
        object, which breaks JSONB key/path queries even though legacy
        readers can decode the string.
        """
        payload = safe(plan)
        if not isinstance(payload, dict):
            log.error("Director DB: strategic plan payload is not an object")
            return False

        result = await self._execute_with_retry(
            "upsert director_strategic_plans",
            lambda: self.supabase.table("director_strategic_plans").upsert({
                "plan_id": plan["plan_id"],
                "objective_id": plan["objective"]["objective_id"],
                "status": "proposed",
                "authority": "none",
                "advisory_only": True,
                "execution_started": False,
                "tasks_created": False,
                "source_context_fingerprint": plan["source_context_fingerprint"],
                "payload": payload,
                "created_at": iso_now(),
                "updated_at": iso_now(),
            }).execute(),
        )
        if result is None:
            return False

        persisted_result = await self._execute_with_retry(
            "verify director_strategic_plans JSONB object",
            lambda: self.supabase.table("director_strategic_plans")
            .select("payload").eq("plan_id", plan["plan_id"]).limit(1).execute(),
        )
        raw_payload = (
            persisted_result.data[0].get("payload")
            if persisted_result and persisted_result.data else None
        )
        if not isinstance(raw_payload, dict) or raw_payload != payload:
            log.error(
                "Director DB: strategic plan JSONB object verification failed "
                "for plan_id=%s",
                plan["plan_id"],
            )
            return False
        return True

    async def get_strategic_plan(self, plan_id: str) -> Optional[dict]:
        result = await self._execute_with_retry(
            "get director_strategic_plans",
            lambda: self.supabase.table("director_strategic_plans")
            .select("payload").eq("plan_id", plan_id).limit(1).execute(),
        )
        if not result or not result.data:
            return None
        payload = result.data[0].get("payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (TypeError, ValueError):
                return None
        return payload if isinstance(payload, dict) else None

    async def list_strategic_plans(self, limit: int = 50) -> List[dict]:
        result = await self._execute_with_retry(
            "list director_strategic_plans",
            lambda: self.supabase.table("director_strategic_plans")
            .select("payload").order("created_at", desc=True).limit(limit).execute(),
        )
        plans = []
        for row in (result.data or []) if result else []:
            payload = row.get("payload")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except (TypeError, ValueError):
                    continue
            if isinstance(payload, dict):
                plans.append(payload)
        return plans

    async def save_observation(self, observation: Observation) -> None:
        workspace_id = str(observation.workspace_id).strip() if observation.workspace_id else None
        scope_type = "workspace" if workspace_id else observation.scope_type
        if scope_type not in {"platform", "workspace", "legacy_unclassified"}:
            log.error("Director DB: refusing observation with invalid ownership scope")
            return
        if scope_type == "workspace" and not workspace_id:
            log.error("Director DB: refusing workspace observation without workspace_id")
            return
        if scope_type != "workspace" and workspace_id:
            log.error("Director DB: refusing non-workspace observation with workspace_id")
            return
        payload = safe(asdict(observation))
        # Observer loops run repeatedly. Fingerprint an unchanged source state
        # so repeated snapshots can update the existing database-generated row.
        volatile_observation_keys = {
            "timestamp", "observed_at", "updated_at", "generated_at",
            "checked_at", "last_checked_at", "last_observed_at",
            "last_updated_at", "age_seconds", "revision",
            "correlation_id", "request_id", "trace_id", "span_id",
            "duration_ms", "latency_ms", "uptime_seconds",
            "process_uptime_seconds",
        }

        def stable_value(value):
            if isinstance(value, dict):
                return {
                    str(key): stable_value(item)
                    for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
                    if str(key).lower() not in volatile_observation_keys
                }
            if isinstance(value, list):
                return [stable_value(item) for item in value]
            return value

        # Keep changing poll metadata in the stored payload for diagnostics,
        # but exclude it from identity so unchanged health state is upserted.
        fingerprint_payload = stable_value(payload)
        identity = {
            "scope_type": scope_type,
            "workspace_id": workspace_id,
            "source": observation.source,
            "state": fingerprint_payload,
        }
        fingerprint = hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()
        # The production table uses a BIGINT identity/sequence primary key.
        # Keep the stable fingerprint in JSONB and let PostgreSQL allocate id.
        # First update an existing fingerprint match; otherwise insert a new row.
        # A concurrent first insert can at worst create a duplicate snapshot, but
        # cannot overwrite an unrelated observation or violate the BIGINT contract.
        payload["observation_fingerprint"] = fingerprint

        def persist_observation():
            existing_query = (
                self.supabase.table("director_observations")
                .select("id")
                .eq("scope_type", scope_type)
                .contains("payload", {"observation_fingerprint": fingerprint})
            )
            if workspace_id:
                existing_query = existing_query.eq("workspace_id", workspace_id)
            else:
                existing_query = existing_query.is_("workspace_id", "null")
            existing = existing_query.limit(1).execute()
            rows = getattr(existing, "data", None) or []
            if rows:
                existing_id = rows[0].get("id")
                return (
                    self.supabase.table("director_observations")
                    .update({
                        "payload": payload,
                        "timestamp": iso(observation.timestamp),
                    })
                    .eq("id", existing_id)
                    .execute()
                )
            return self.supabase.table("director_observations").insert({
                "workspace_id": workspace_id,
                "scope_type": scope_type,
                "payload": payload,
                "timestamp": iso(observation.timestamp),
            }).execute()

        result = await self._execute_with_retry(
            "upsert deduplicated director observation",
            persist_observation,
        )
        if result is not None:
            await self._prune_recent_records(
                "director_observations", self.MAX_PERSISTED_OBSERVATIONS,
                scope_type=scope_type, workspace_id=workspace_id,
            )

    async def list_observations(
        self, limit: int = 100, *, workspace_id: Optional[str] = None,
        scope_type: str = "platform",
    ) -> List[dict]:
        """List observations from exactly one ownership scope."""
        if scope_type not in {"platform", "workspace", "legacy_unclassified"}:
            raise ValueError("invalid Director observation scope")
        if scope_type == "workspace" and not (workspace_id and workspace_id.strip()):
            raise ValueError("workspace_id is required for workspace observation reads")
        if scope_type != "workspace" and workspace_id is not None:
            raise ValueError("workspace_id cannot be combined with a non-workspace scope")
        query = self.supabase.table("director_observations").select("*")
        query = query.eq("scope_type", scope_type)
        if scope_type == "workspace":
            query = query.eq("workspace_id", workspace_id.strip())
        result = await self._execute_with_retry(
            "list Director observations by ownership scope",
            lambda: query.order("timestamp", desc=True).limit(limit).execute(),
        )
        return [dict(row) for row in (result.data or [])] if result else []

    async def save_approval(self, task_id: str, approved: bool, actor: str) -> None:
        await self._execute_with_retry(
            "insert into director_approvals",
            lambda: self.supabase.table("director_approvals").upsert({
                "task_id": task_id,
                "approved": approved,
                "actor": actor,
                "timestamp": iso_now(),
            }).execute(),
        )

    async def get_approval(self, task_id: str) -> Optional[dict]:
        result = await self._execute_with_retry(
            "get from director_approvals",
            lambda: self.supabase.table("director_approvals")
            .select("*").eq("task_id", task_id).limit(1).execute(),
        )
        return dict(result.data[0]) if result and result.data else None

    # ──────────────────────────────────────────────────────────────────
    # LEARNING SIGNALS
    # ──────────────────────────────────────────────────────────────────

    async def save_learning(self, entry: dict) -> bool:
        """Persist verified learning and report whether the write was acknowledged."""
        if not isinstance(entry, dict):
            return False
        context = entry.get("context")
        if (
            entry.get("verification_status") != "verified"
            or not isinstance(context, dict)
            or context.get("status") != "verified"
            or entry.get("outcome") != "success"
            or not entry.get("id")
        ):
            log.info("Director DB: rejected non-verified learning record")
            return False
        result = await self._execute_with_retry(
            "insert verified Director learning",
            lambda: self.supabase.table("director_learnings").upsert({
                "id": entry["id"],
                "payload": safe(entry),
                "timestamp": iso_now(),
            }).execute(),
        )
        if result is None:
            return False
        await self._prune_recent_records("director_learnings", self.MAX_PERSISTED_LEARNINGS)
        return True

    async def list_learnings(self, limit: int = 100) -> List[dict]:
        result = await self._execute_with_retry(
            "list from director_learnings",
            lambda: self.supabase.table("director_learnings")
            .select("*").order("timestamp", desc=True).limit(limit).execute(),
        )
        return [dict(row) for row in (result.data or [])] if result else []

    async def list_learnings_since(
        self,
        since: datetime,
        limit: int = 5000,
    ) -> List[dict]:
        """Load learning signals newer than `since`, in chronological order.

        Returns the raw payload dicts as stored. The LearningLoop consumes
        these at Director startup to rebuild in-memory state after a
        process restart — see Director._replay_learning_history.

        The limit is generous (5000) because replay is a rare operation and
        we prefer completeness over a bounded read. If a deployment
        generates more than 5000 signals in the lookback window, replay
        will silently truncate to the most recent entries.
        """
        result = await self._execute_with_retry(
            "list from director_learnings (since)",
            lambda: self.supabase.table("director_learnings")
            .select("id, payload, timestamp")
            .gte("timestamp", iso(since))
            .order("timestamp", desc=False)  # chronological
            .limit(limit)
            .execute(),
        )
        if not result or not result.data:
            return []
        out: List[dict] = []
        for row in result.data:
            payload = row.get("payload")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except Exception:
                    continue
            if isinstance(payload, dict):
                payload = dict(payload)
                payload.setdefault("id", row.get("id"))
                out.append(payload)
        return out


# ═══════════════════════════════════════════════════════════════════════════
# IN-MEMORY STORE
# ═══════════════════════════════════════════════════════════════════════════

class InMemoryStore:
    """Bounded in-memory store for when Supabase is unavailable.

    All collections are capped and evict oldest entries on overflow. This
    is intentional — the Director must not grow unbounded when running
    without durable storage.
    """

    MAX_TASKS = 500
    MAX_DECISIONS = 200
    MAX_OBSERVATIONS = 500
    MAX_APPROVALS = 200
    MAX_LEARNINGS = 500

    def __init__(self):
        self.tasks: Dict[str, dict] = {}
        self.decisions: deque = deque(maxlen=self.MAX_DECISIONS)
        self.observations: deque = deque(maxlen=self.MAX_OBSERVATIONS)
        self.approvals: Dict[str, dict] = {}
        self.learnings: deque = deque(maxlen=self.MAX_LEARNINGS)
        self.strategic_plans: Dict[str, dict] = {}

    async def save_task(self, task: Task) -> bool:
        if len(self.tasks) >= self.MAX_TASKS:
            completed = [
                tid for tid, t in self.tasks.items()
                if t.get("status") in ("completed", "failed", "cancelled")
            ]
            for tid in completed[:100]:
                del self.tasks[tid]
        record = safe(asdict(task))
        record["scope_type"] = "workspace" if task.workspace_id else task.scope_type
        if record["scope_type"] not in {"platform", "workspace", "legacy_unclassified"}:
            return False
        if record["scope_type"] == "workspace" and not task.workspace_id:
            return False
        self.tasks[task.id] = record
        return True

    async def get_task(self, task_id: str) -> Optional[dict]:
        return self.tasks.get(task_id)

    async def list_tasks(
        self, limit: int = 200, *, workspace_id: Optional[str] = None,
        scope_type: str = "platform",
    ) -> List[dict]:
        if scope_type not in {"platform", "workspace", "legacy_unclassified"}:
            raise ValueError("invalid Director task scope")
        if scope_type == "workspace" and not (workspace_id and workspace_id.strip()):
            raise ValueError("workspace_id is required for workspace task reads")
        rows = []
        for row in self.tasks.values():
            row_scope = row.get("scope_type", "platform")
            if row_scope != scope_type:
                continue
            if scope_type == "workspace" and row.get("workspace_id") != workspace_id.strip():
                continue
            rows.append(row)
        return rows[-limit:]

    async def list_approved_workspace_tasks(self, limit: int = 5000) -> List[dict]:
        """Internal recovery scan; returns only approved tenant task rows."""
        rows = [
            row for row in self.tasks.values()
            if row.get("scope_type") == "workspace" and row.get("status") == Status.APPROVED.value
        ]
        return rows[-max(1, min(int(limit), 5000)):]

    async def save_decision(self, decision: Decision) -> None:
        self.decisions.append(safe(asdict(decision)))

    async def list_decisions(self, limit: int = 100) -> List[dict]:
        return list(self.decisions)[-limit:]

    MAX_STRATEGIC_PLANS = 100

    async def save_strategic_plan(self, plan: dict) -> bool:
        self.strategic_plans[plan["plan_id"]] = safe(plan)
        while len(self.strategic_plans) > self.MAX_STRATEGIC_PLANS:
            self.strategic_plans.pop(next(iter(self.strategic_plans)))
        return True

    async def get_strategic_plan(self, plan_id: str) -> Optional[dict]:
        plan = self.strategic_plans.get(plan_id)
        return safe(plan) if plan is not None else None

    async def list_strategic_plans(self, limit: int = 50) -> List[dict]:
        return [safe(plan) for plan in list(self.strategic_plans.values())[-limit:][::-1]]

    async def save_observation(self, observation: Observation) -> None:
        self.observations.append(safe(asdict(observation)))

    async def list_observations(
        self, limit: int = 100, *, workspace_id: Optional[str] = None,
        scope_type: str = "platform",
    ) -> List[dict]:
        if scope_type not in {"platform", "workspace", "legacy_unclassified"}:
            raise ValueError("invalid Director observation scope")
        if scope_type == "workspace" and not (workspace_id and workspace_id.strip()):
            raise ValueError("workspace_id is required for workspace observation reads")
        if scope_type != "workspace" and workspace_id is not None:
            raise ValueError("workspace_id cannot be combined with a non-workspace scope")
        rows = []
        for row in self.observations:
            row_scope = row.get("scope_type", "platform")
            if row_scope != scope_type:
                continue
            if scope_type == "workspace" and row.get("workspace_id") != workspace_id.strip():
                continue
            rows.append(row)
        return rows[-limit:]

    async def save_approval(self, task_id: str, approved: bool, actor: str) -> None:
        if len(self.approvals) >= self.MAX_APPROVALS:
            oldest = list(self.approvals.keys())[:100]
            for key in oldest:
                del self.approvals[key]
        self.approvals[task_id] = {
            "task_id": task_id,
            "approved": approved,
            "actor": actor,
            "timestamp": iso_now(),
        }

    async def get_approval(self, task_id: str) -> Optional[dict]:
        return self.approvals.get(task_id)

    async def save_learning(self, entry: dict) -> bool:
        context = entry.get("context") if isinstance(entry, dict) else None
        if (
            not isinstance(entry, dict)
            or entry.get("verification_status") != "verified"
            or not isinstance(context, dict)
            or context.get("status") != "verified"
            or entry.get("outcome") != "success"
            or not entry.get("id")
        ):
            return False
        self.learnings.append(entry)
        return True

    async def list_learnings(self, limit: int = 100) -> List[dict]:
        return list(self.learnings)[-limit:]

    async def list_learnings_since(
        self,
        since: datetime,
        limit: int = 5000,
    ) -> List[dict]:
        """In-memory equivalent of AsyncSupabaseStore.list_learnings_since.

        The in-memory store does not persist across restarts, so this is
        primarily useful in tests and in local dev where the Director is
        booted without a Supabase client. Filters on the entry's
        `timestamp` field when present; entries without one are included
        (they are almost certainly recent).
        """
        cutoff = iso(since)
        out: List[dict] = []
        for entry in self.learnings:
            ts = entry.get("timestamp")
            if ts is None or str(ts) >= cutoff:
                out.append(entry)
        return out[-limit:]


__all__ = [
    "AsyncSupabaseStore",
    "InMemoryStore",
]
