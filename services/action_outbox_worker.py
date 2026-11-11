"""Durable multi-process worker for governed ActionIntent outbox rows."""
from __future__ import annotations
import asyncio
import logging
import os
import uuid
from typing import Any

logger = logging.getLogger("kanyoza.action_outbox_worker")

class ActionOutboxWorker:
    def __init__(self, db=None, *, batch_size: int = 20, lease_seconds: int = 120):
        self.db = db
        self.batch_size = max(1, min(int(batch_size), 100))
        self.lease_seconds = max(30, min(int(lease_seconds), 600))
        self.worker_id = f"action-worker:{os.getpid()}:{uuid.uuid4().hex[:12]}"

    def _db_client(self):
        if self.db is not None:
            return self.db
        try:
            from services.supabase import supabase_service
            self.db = supabase_service.service_client or supabase_service.client
        except Exception:
            self.db = None
        return self.db

    async def claim(self) -> list[dict[str, Any]]:
        db = self._db_client()
        if db is None:
            raise RuntimeError("durable action outbox database is required")
        result = await asyncio.to_thread(lambda: db.rpc("claim_action_outbox", {
            "p_limit": self.batch_size, "p_lease_seconds": self.lease_seconds, "p_worker_id": self.worker_id,
        }).execute())
        data = result.data or []
        return data if isinstance(data, list) else [data]

    async def _renew_lease(self, row: dict[str, Any]) -> bool:
        """Renew the claimed outbox lease using all ownership fences."""
        db = self._db_client()
        if db is None or not row.get("lease_token"):
            return False
        result = await asyncio.to_thread(lambda: db.rpc("renew_action_outbox_lease", {
            "p_intent_id": row.get("intent_id"),
            "p_workspace_id": row.get("workspace_id"),
            "p_lease_token": row.get("lease_token"),
            "p_lease_seconds": self.lease_seconds,
        }).execute())
        return bool(getattr(result, "data", False))

    async def _heartbeat(self, row: dict[str, Any], stop: asyncio.Event, *, interval: int | None = None) -> None:
        """Keep an owned outbox lease live while a slow action is executing.

        A failed renewal is deliberately non-authoritative: execution ownership
        remains fenced by the Execution Ledger and final outbox finalization still
        requires the original lease token.
        """
        interval = interval if interval is not None else max(10, self.lease_seconds // 3)
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                try:
                    if not await self._renew_lease(row):
                        logger.warning("action outbox lease renewal rejected intent=%s", row.get("intent_id"))
                        return
                except Exception:
                    logger.exception("action outbox lease heartbeat failed intent=%s", row.get("intent_id"))
                    return

    async def process_row(self, row: dict[str, Any]) -> dict[str, Any]:
        from services.action_kernel import build_default_action_kernel
        db = self._db_client()
        kernel = build_default_action_kernel(db, allow_in_memory=False)
        heartbeat_stop = asyncio.Event()

        heartbeat_task = asyncio.create_task(self._heartbeat(row, heartbeat_stop)) if row.get("lease_token") else None
        try:
            result = await kernel.process_outbox_row(row)
            status = str(result.get("status") or "")
            # A recovery release is control-plane evidence only. Close it as
            # recovered *only* after this exact outbox intent reports terminal
            # success. The DB RPC re-checks plan, workspace, intent, execution,
            # and authoritative outbox state under row locks.
            if status == "succeeded" and row.get("recovery_plan_id"):
                try:
                    from services.v67_recovery_lifecycle import recovery_success_rpc_payload
                    closure = await asyncio.to_thread(lambda: db.rpc("mark_recovery_recovered", recovery_success_rpc_payload(
                        plan_id=str(row.get("recovery_plan_id")),
                        intent_id=str(row.get("intent_id") or ""),
                        workspace_id=str(row.get("workspace_id") or ""),
                        execution_id=str(result.get("execution_id") or "") or None,
                    )).execute())
                    result["recovery_closure"] = {"ok": bool(getattr(closure, "data", False)), "state": "recovered" if getattr(closure, "data", False) else "not_closed"}
                except Exception as exc:
                    # Do not turn an actual execution success into a false
                    # execution failure. Closure is control-plane bookkeeping;
                    # its failure creates repairable recovery-state debt.
                    logger.exception("failed to close recovery lifecycle intent=%s", row.get("intent_id"))
                    result["recovery_closure"] = {"ok": False, "state": "closure_pending", "error": str(exc)[:500]}
            if status in {"failed", "uncertain", "reconciliation_required"}:
                contract = kernel._contracts.get(str(row.get("action_type") or ""))
                from services.autonomous_exception_recovery import RecoveryCoordinator
                coordinator = RecoveryCoordinator(db)
                recovery = await coordinator.handle_failure(
                    row=row, result=result,
                    idempotent=bool(contract and contract.autonomous_retryable),
                    irreversible=bool(contract and contract.irreversible),
                    requires_approval=bool(contract and contract.requires_approval),
                    has_reconciler=bool(contract and contract.reconciler),
                )
                result["recovery"] = recovery
            return result
        except Exception as exc:
            intent_id = str(row.get("intent_id") or "")
            workspace_id = str(row.get("workspace_id") or "")
            error_message = str(exc)[:1000]
            logger.exception("action intent failed intent=%s: %s", intent_id, exc)
            # A worker crash/transport exception is deliberately represented as
            # reconciliation_required rather than a false success. The execution
            # ledger is authoritative for whether an external side effect occurred.
            failure_result = {
                "ok": False,
                "status": "reconciliation_required",
                "intent_id": intent_id,
                "execution_id": str(row.get("execution_id") or intent_id),
                "error": error_message,
                "reconciliation_required": True,
            }
            if intent_id and workspace_id and row.get("lease_token"):
                try:
                    await asyncio.to_thread(lambda: db.rpc("finalize_action_outbox", {
                        "p_intent_id": intent_id, "p_workspace_id": workspace_id,
                        "p_lease_token": row.get("lease_token"), "p_status": "reconciliation_required",
                        "p_error": error_message,
                    }).execute())
                except Exception:
                    logger.exception("failed to persist action uncertainty intent=%s", intent_id)
                # A worker-level exception used to terminate here without entering
                # V67. That created a blind spot: failures occurring outside the
                # normal kernel result path received no durable exception evidence
                # or recovery classification. Feed the same conservative
                # uncertainty signal into the recovery coordinator. Because the
                # result is explicitly reconciliation_required, V67 can never
                # turn this path into a blind autonomous retry.
                try:
                    contract = kernel._contracts.get(str(row.get("action_type") or ""))
                    from services.autonomous_exception_recovery import RecoveryCoordinator
                    recovery = await RecoveryCoordinator(db).handle_failure(
                        row=row, result=failure_result,
                        idempotent=bool(contract and contract.autonomous_retryable),
                        irreversible=bool(contract and contract.irreversible),
                        requires_approval=bool(contract and contract.requires_approval),
                        has_reconciler=bool(contract and contract.reconciler),
                    )
                    failure_result["recovery"] = recovery
                except Exception:
                    logger.exception("failed to persist/classify V67 recovery intent=%s", intent_id)
            return failure_result
        finally:
            heartbeat_stop.set()
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass

    async def run_once(self) -> dict[str, Any]:
        rows = await self.claim()
        results = []
        for row in rows:
            results.append(await self.process_row(row))
        return {"claimed": len(rows), "results": results}

    async def run(self, stop_event: asyncio.Event, interval_seconds: int = 2) -> None:
        while not stop_event.is_set():
            try:
                result = await self.run_once()
                if result["claimed"]:
                    logger.info("action outbox processed=%d", result["claimed"])
            except Exception:
                logger.exception("action outbox dispatch cycle failed")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            except asyncio.TimeoutError:
                pass

async def run(stop_event: asyncio.Event, interval_seconds: int = 2) -> None:
    await ActionOutboxWorker().run(stop_event, interval_seconds=interval_seconds)
