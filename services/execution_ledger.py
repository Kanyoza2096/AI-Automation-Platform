"""Durable execution ledger for externally visible side effects.

The ledger records intent, attempts and terminal/ambiguous outcomes. It is
provider-agnostic and deliberately does not claim exactly-once delivery; it
provides a durable idempotency boundary and evidence trail for at-least-once
execution.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
import socket
from datetime import timedelta
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger("kanyoza.execution_ledger")


# ── Workspace → UUID adapter ──────────────────────────────────────────────
#
# The durable ledger column `workspace_id` is text, but the durable RPCs
# (`begin_external_execution`, `claim_external_execution`, etc.) currently
# declare `p_workspace_id` as uuid. Sending the raw workspace string trips
# Postgres 22P02 before the value ever reaches the table.
#
# To keep both ends happy we map any workspace string to a stable UUID v5.
# The mapping is deterministic (same input → same UUID) and fully reversible:
# any read that filters by workspace_id can use the same helper.
#
def _workspace_uuid(workspace_id: str) -> str:
    """Map a workspace string to a stable UUID v5.

    If the value already parses as a UUID it is returned unchanged; otherwise
    a namespace-scoped UUID v5 is derived from the original string so the
    same workspace always produces the same UUID.
    """
    try:
        return str(uuid.UUID(str(workspace_id)))
    except (ValueError, AttributeError, TypeError):
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, str(workspace_id)))


@dataclass(frozen=True)
class ExecutionRecord:
    execution_id: str
    workspace_id: str
    action: str
    idempotency_key: str
    status: str
    attempt: int = 0
    provider: Optional[str] = None
    provider_request_id: Optional[str] = None
    provider_object_id: Optional[str] = None
    error: Optional[str] = None
    evidence: Optional[dict[str, Any]] = None
    locked_by: Optional[str] = None
    locked_until: Optional[str] = None


class ExecutionLedger:
    """Small persistence adapter; DB is authoritative in production."""

    def __init__(self, db=None, *, allow_in_memory: bool = False):
        self._db = db
        self._allow_in_memory = allow_in_memory
        self._memory: dict[str, ExecutionRecord] = {}
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"

    @staticmethod
    def make_idempotency_key(*parts: str) -> str:
        material = "|".join((p or "").strip() for p in parts)
        return "exec_" + hashlib.sha256(material.encode("utf-8")).hexdigest()

    def begin(self, *, workspace_id: str, action: str, idempotency_key: str,
              provider: Optional[str] = None, metadata: Optional[dict[str, Any]] = None) -> ExecutionRecord:
        if not workspace_id:
            raise ValueError("workspace_id is required")
        if not idempotency_key:
            raise ValueError("idempotency_key is required")
        if self._db:
            try:
                res = self._db.rpc("begin_external_execution", {
                    "p_workspace_id": _workspace_uuid(workspace_id),
                    "p_action": action,
                    "p_idempotency_key": idempotency_key,
                    "p_provider": provider,
                    "p_metadata": metadata or {},
                }).execute()
                if res.data:
                    return self._from_row(res.data[0] if isinstance(res.data, list) else res.data)
                # A configured durable ledger is authoritative. Never mint a
                # process-local execution record when the durable begin RPC did
                # not return an authoritative row; doing so can permit a duplicate
                # external side effect after a transient DB/RPC failure.
                raise RuntimeError("Execution ledger begin returned no authoritative record")
            except Exception as exc:
                logger.warning("execution ledger begin failed: %s", exc)
                try:
                    from config.settings import settings
                    if getattr(settings, "is_production", False):
                        raise RuntimeError(f"Execution ledger unavailable: {exc}") from exc
                except ImportError:
                    pass
                if self._db is not None:
                    raise RuntimeError(f"Execution ledger begin unavailable: {exc}") from exc
        else:
            try:
                from config.settings import settings
                if getattr(settings, "is_production", False) and not self._allow_in_memory:
                    raise RuntimeError("Durable execution ledger database is required in production")
            except ImportError:
                pass
        existing = self._memory.get(idempotency_key)
        if existing:
            return existing
        record = ExecutionRecord(
            execution_id="exe_" + uuid.uuid4().hex,
            workspace_id=workspace_id,
            action=action,
            idempotency_key=idempotency_key,
            status="started",
            provider=provider,
            locked_by=self.worker_id,
            locked_until=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
        )
        self._memory[idempotency_key] = record
        return record

    def claim(self, record: ExecutionRecord, *, lease_seconds: int = 300) -> ExecutionRecord:
        """Acquire the execution lease before invoking an external provider.

        A live lease means another worker owns the intent; callers must not
        execute concurrently. An expired in-flight lease becomes UNCERTAIN
        rather than being blindly reclaimed, because the provider may already
        have received the request before the worker died.
        """
        if self._db:
            try:
                res = self._db.rpc("claim_external_execution", {
                    "p_execution_id": record.execution_id,
                    "p_worker_id": self.worker_id,
                    "p_lease_seconds": int(lease_seconds),
                }).execute()
                if res.data:
                    return self._from_row(res.data[0] if isinstance(res.data, list) else res.data)
                # A durable ledger is authoritative. An empty claim result is not
                # permission to fall back to process memory: another worker may own
                # the lease, or the RPC may have rejected the claim. Executing here
                # could create a duplicate external side effect.
                raise RuntimeError("Execution ledger claim returned no authoritative record")
            except Exception as exc:
                logger.warning("execution ledger claim failed: %s", exc)
                try:
                    from config.settings import settings
                    if getattr(settings, "is_production", False):
                        raise RuntimeError(f"Execution ledger claim unavailable: {exc}") from exc
                except ImportError:
                    pass
                if self._db is not None:
                    # Durable mode must fail closed even outside production tests.
                    # The caller can retry the claim later; it must not execute from
                    # an uncoordinated local mirror.
                    raise RuntimeError(f"Execution ledger claim unavailable: {exc}") from exc
        current = self._memory.get(record.idempotency_key, record)
        if current.locked_by and current.locked_by != self.worker_id:
            if current.locked_until:
                try:
                    lease_until = datetime.fromisoformat(current.locked_until)
                    if lease_until <= datetime.now(timezone.utc) and current.status == "started":
                        current = ExecutionRecord(
                            current.execution_id, current.workspace_id, current.action,
                            current.idempotency_key, "uncertain", current.attempt,
                            current.provider, current.provider_request_id, current.provider_object_id,
                            "Execution lease expired before completion; reconciliation required",
                            {**(current.evidence or {}), "lease_expired": True}, None, None,
                        )
                        self._memory[current.idempotency_key] = current
                        return current
                except ValueError:
                    return current
            return current
        if current.status == "started":
            updated = ExecutionRecord(
                current.execution_id, current.workspace_id, current.action,
                current.idempotency_key, current.status, current.attempt,
                current.provider, current.provider_request_id, current.provider_object_id,
                current.error, current.evidence, self.worker_id,
                (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat(),
            )
            self._memory[current.idempotency_key] = updated
            return updated
        return current


    def renew(self, record: ExecutionRecord, *, lease_seconds: int = 300) -> ExecutionRecord:
        """Renew a live durable lease without allowing stale-worker resurrection."""
        if lease_seconds < 30 or lease_seconds > 3600:
            raise ValueError("lease_seconds must be between 30 and 3600")
        if self._db:
            try:
                res = self._db.rpc("renew_external_execution", {
                    "p_execution_id": record.execution_id,
                    "p_worker_id": self.worker_id,
                    "p_lease_seconds": int(lease_seconds),
                }).execute()
                if res.data:
                    return self._from_row(res.data[0] if isinstance(res.data, list) else res.data)
                raise RuntimeError("Execution lease renewal rejected or expired")
            except Exception as exc:
                raise RuntimeError(f"Execution lease renewal unavailable: {exc}") from exc
        current = self._memory.get(record.idempotency_key, record)
        if current.locked_by != self.worker_id or current.status != "started":
            raise RuntimeError("Execution ownership lost; lease cannot be renewed")
        current = ExecutionRecord(
            current.execution_id, current.workspace_id, current.action,
            current.idempotency_key, current.status, current.attempt,
            current.provider, current.provider_request_id, current.provider_object_id,
            current.error, current.evidence, self.worker_id,
            (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat(),
        )
        self._memory[current.idempotency_key] = current
        return current

    def complete(self, record: ExecutionRecord, *, status: str,
                 provider_request_id: Optional[str] = None,
                 provider_object_id: Optional[str] = None,
                 error: Optional[str] = None,
                 evidence: Optional[dict[str, Any]] = None) -> ExecutionRecord:
        if status not in {"succeeded", "failed", "uncertain", "cancelled"}:
            raise ValueError("invalid execution terminal status")
        updated = ExecutionRecord(record.execution_id, record.workspace_id, record.action,
            record.idempotency_key, status, record.attempt + 1, record.provider,
            provider_request_id, provider_object_id, error,
            evidence or {}, None, None)
        if self._db:
            try:
                res = self._db.rpc("complete_external_execution", {
                    "p_execution_id": updated.execution_id,
                    "p_worker_id": self.worker_id,
                    "p_status": status,
                    "p_provider_request_id": provider_request_id,
                    "p_provider_object_id": provider_object_id,
                    "p_error": error,
                    "p_evidence": evidence or {},
                }).execute()
                if not getattr(res, "data", None):
                    raise RuntimeError("Execution ledger completion returned no authoritative record")
            except Exception as exc:
                logger.error("execution ledger completion failed id=%s: %s", updated.execution_id, exc)
                try:
                    from config.settings import settings
                    if getattr(settings, "is_production", False):
                        raise RuntimeError(f"Execution ledger completion failed: {exc}") from exc
                except ImportError:
                    pass
                if self._db is not None:
                    raise RuntimeError(f"Execution ledger completion unavailable: {exc}") from exc
        else:
            try:
                from config.settings import settings
                if getattr(settings, "is_production", False) and not self._allow_in_memory:
                    raise RuntimeError("Durable execution ledger database is required in production")
            except ImportError:
                pass
        self._memory[record.idempotency_key] = updated
        return updated

    @staticmethod
    def _from_row(row: dict[str, Any]) -> ExecutionRecord:
        return ExecutionRecord(
            execution_id=row["execution_id"], workspace_id=row["workspace_id"],
            action=row["action"], idempotency_key=row["idempotency_key"],
            status=row.get("status", "started"), attempt=int(row.get("attempt") or 0),
            provider=row.get("provider"), provider_request_id=row.get("provider_request_id"),
            provider_object_id=row.get("provider_object_id"), error=row.get("error"),
            evidence=row.get("evidence") or {},
            locked_by=row.get("locked_by"), locked_until=row.get("locked_until"),
        )
