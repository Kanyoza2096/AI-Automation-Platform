"""Durable multi-process worker for governed ActionIntent outbox rows."""
from __future__ import annotations
import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger("kanyoza.action_outbox_worker")

class ActionOutboxWorker:
    def __init__(self, db=None, *, batch_size: int = 20, lease_seconds: int = 120):
        self.db = db
        self.batch_size = max(1, min(int(batch_size), 100))
        self.lease_seconds = max(30, min(int(lease_seconds), 600))
        self.worker_id = f"action-worker:{os.getpid()}"

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

    async def process_row(self, row: dict[str, Any]) -> dict[str, Any]:
        from services.action_kernel import build_default_action_kernel
        db = self._db_client()
        kernel = build_default_action_kernel(db, allow_in_memory=False)
        try:
            return await kernel.process_outbox_row(row)
        except Exception as exc:
            intent_id = str(row.get("intent_id") or "")
            workspace_id = str(row.get("workspace_id") or "")
            logger.exception("action intent failed intent=%s: %s", intent_id, exc)
            # A worker crash/transport exception is deliberately represented as
            # reconciliation_required rather than a false success. The execution
            # ledger is authoritative for whether an external side effect occurred.
            if intent_id and workspace_id and row.get("lease_token"):
                try:
                    await asyncio.to_thread(lambda: db.rpc("finalize_action_outbox", {
                        "p_intent_id": intent_id, "p_workspace_id": workspace_id,
                        "p_lease_token": row.get("lease_token"), "p_status": "reconciliation_required",
                        "p_error": str(exc)[:1000],
                    }).execute())
                except Exception:
                    logger.exception("failed to persist action uncertainty intent=%s", intent_id)
            return {"ok": False, "status": "reconciliation_required", "intent_id": intent_id, "error": str(exc)[:1000]}

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
