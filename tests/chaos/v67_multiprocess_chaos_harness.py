"""Deterministic multi-process chaos harness for V67.

This harness models the durable ownership state machine without requiring a live
Supabase instance. It is deliberately conservative: provider success followed
by a crash is represented as UNCERTAIN unless a durable receipt is committed.
The harness is a pre-staging gate, not a substitute for real database chaos.
"""
from __future__ import annotations

import multiprocessing as mp
import os
import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ChaosResult:
    winner_count: int
    provider_calls: int
    final_status: str
    uncertain: bool
    duplicate_prevented: bool
    worker_pids: tuple[int, ...]


class SharedOutbox:
    """Small atomic state machine used only by the chaos harness."""

    def __init__(self, manager: Any):
        self.lock = manager.Lock()
        self.state = manager.dict({
            "outbox": "pending",
            "owner": "",
            "provider_calls": 0,
            "receipt": "",
            "workers": [],
        })

    def claim(self, worker_id: str) -> bool:
        with self.lock:
            if self.state["outbox"] != "pending":
                return False
            self.state["outbox"] = "processing"
            self.state["owner"] = worker_id
            workers = list(self.state["workers"])
            workers.append(os.getpid())
            self.state["workers"] = workers
            return True

    def provider_call(self) -> None:
        with self.lock:
            self.state["provider_calls"] = int(self.state["provider_calls"]) + 1

    def crash_to_uncertain(self) -> None:
        with self.lock:
            if self.state["receipt"] != "succeeded":
                self.state["outbox"] = "reconciliation_required"

    def persist_receipt(self) -> None:
        with self.lock:
            self.state["receipt"] = "succeeded"
            self.state["outbox"] = "succeeded"


def _worker(shared: SharedOutbox, barrier: Any, crash_point: str) -> None:
    worker_id = f"chaos:{os.getpid()}"
    barrier.wait()
    if not shared.claim(worker_id):
        return
    if crash_point == "before_provider":
        return
    shared.provider_call()
    if crash_point == "after_provider_before_receipt":
        shared.crash_to_uncertain()
        return
    shared.persist_receipt()


def run_race(*, workers: int = 4, crash_point: str = "none") -> ChaosResult:
    context = mp.get_context("spawn")
    manager = context.Manager()
    try:
        shared = SharedOutbox(manager)
        barrier = manager.Barrier(workers)
        processes = [
            context.Process(target=_worker, args=(shared, barrier, crash_point))
            for _ in range(workers)
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=5)
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join()

        provider_calls = int(shared.state["provider_calls"])
        final_status = str(shared.state["outbox"])
        winner_count = len(shared.state["workers"])
        return ChaosResult(
            winner_count=winner_count,
            provider_calls=provider_calls,
            final_status=final_status,
            uncertain=final_status == "reconciliation_required",
            duplicate_prevented=provider_calls <= 1,
            worker_pids=tuple(shared.state["workers"]),
        )
    finally:
        manager.shutdown()
