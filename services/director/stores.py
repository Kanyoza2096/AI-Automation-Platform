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
import uuid
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
        result = await self._execute_with_retry(
            "insert into director_tasks",
            lambda: self.supabase.table("director_tasks").upsert({
                "id": task.id,
                "payload": json.dumps(safe(asdict(task))),
                "created_at": iso(task.created_at),
                "status": task.status.value,
            }).execute(),
        )
        return result is not None

    async def get_task(self, task_id: str) -> Optional[dict]:
        result = await self._execute_with_retry(
            "get from director_tasks",
            lambda: self.supabase.table("director_tasks")
            .select("*").eq("id", task_id).limit(1).execute(),
        )
        return dict(result.data[0]) if result and result.data else None

    async def list_tasks(self, limit: int = 200) -> List[dict]:
        result = await self._execute_with_retry(
            "list from director_tasks",
            lambda: self.supabase.table("director_tasks")
            .select("*").order("created_at", desc=True).limit(limit).execute(),
        )
        return [dict(row) for row in (result.data or [])] if result else []

    async def save_decision(self, decision: Decision) -> None:
        await self._execute_with_retry(
            "insert into director_decisions",
            lambda: self.supabase.table("director_decisions").upsert({
                "id": decision.id,
                "payload": json.dumps(safe(asdict(decision))),
                "timestamp": iso(decision.timestamp),
            }).execute(),
        )

    async def list_decisions(self, limit: int = 100) -> List[dict]:
        result = await self._execute_with_retry(
            "list from director_decisions",
            lambda: self.supabase.table("director_decisions")
            .select("*").order("timestamp", desc=True).limit(limit).execute(),
        )
        return [dict(row) for row in (result.data or [])] if result else []

    async def save_observation(self, observation: Observation) -> None:
        await self._execute_with_retry(
            "insert into director_observations",
            lambda: self.supabase.table("director_observations").insert({
                "payload": json.dumps(safe(asdict(observation))),
                "timestamp": iso(observation.timestamp),
            }).execute(),
        )

    async def list_observations(self, limit: int = 100) -> List[dict]:
        result = await self._execute_with_retry(
            "list from director_observations",
            lambda: self.supabase.table("director_observations")
            .select("*").order("timestamp", desc=True).limit(limit).execute(),
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

    async def save_learning(self, entry: dict) -> None:
        """Persist one learning signal.

        `entry` is expected to be the asdict() of a LearningSignal, which
        carries capability, sector, outcome, duration_ms, error_type,
        context (including target and error_class), and timestamp. The
        payload column stores it as opaque JSON — no schema change needed
        when the context dict grows.
        """
        await self._execute_with_retry(
            "insert into director_learnings",
            lambda: self.supabase.table("director_learnings").upsert({
                "id": entry.get("id", f"learn_{uuid.uuid4().hex[:12]}"),
                "payload": json.dumps(safe(entry)),
                "timestamp": iso_now(),
            }).execute(),
        )

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
            .select("payload, timestamp")
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

    async def save_task(self, task: Task) -> bool:
        if len(self.tasks) >= self.MAX_TASKS:
            completed = [
                tid for tid, t in self.tasks.items()
                if t.get("status") in ("completed", "failed", "cancelled")
            ]
            for tid in completed[:100]:
                del self.tasks[tid]
        self.tasks[task.id] = safe(asdict(task))
        return True

    async def get_task(self, task_id: str) -> Optional[dict]:
        return self.tasks.get(task_id)

    async def list_tasks(self) -> List[dict]:
        return list(self.tasks.values())

    async def save_decision(self, decision: Decision) -> None:
        self.decisions.append(safe(asdict(decision)))

    async def list_decisions(self, limit: int = 100) -> List[dict]:
        return list(self.decisions)[-limit:]

    async def save_observation(self, observation: Observation) -> None:
        self.observations.append(safe(asdict(observation)))

    async def list_observations(self, limit: int = 100) -> List[dict]:
        return list(self.observations)[-limit:]

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

    async def save_learning(self, entry: dict) -> None:
        self.learnings.append(entry)

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
