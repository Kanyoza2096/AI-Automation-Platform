"""Controlled provider sandbox for V67.10 external-side-effect chaos tests.

This module is deliberately not a production connector. It models a provider
that can commit an external side effect and then lose the response, while still
supporting provider-side idempotency and reconciliation by idempotency key.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


FAILURE_MODES = {
    "success",
    "reject_before_commit",
    "timeout_after_commit",
    "crash_after_commit",
}


@dataclass(frozen=True)
class SandboxReceipt:
    idempotency_key: str
    provider_object_id: str
    request_id: str
    status: str = "committed"


@dataclass
class SandboxProvider:
    """Deterministic, idempotent fake external provider.

    The lock represents the provider's atomic idempotency boundary. Once a key
    is committed, every later submission for that key returns the same object
    instead of creating a duplicate side effect.
    """

    provider_name: str = "kdo-v67.10-sandbox"
    sandbox: bool = True
    default_failure_mode: str = "success"
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _committed: dict[str, SandboxReceipt] = field(default_factory=dict, init=False, repr=False)
    _calls: list[str] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.default_failure_mode not in FAILURE_MODES:
            raise ValueError(f"unsupported failure mode: {self.default_failure_mode}")

    @property
    def call_count(self) -> int:
        with self._lock:
            return len(self._calls)

    @property
    def committed_count(self) -> int:
        with self._lock:
            return len(self._committed)

    def submit(self, *, idempotency_key: str, failure_mode: str | None = None) -> dict[str, Any]:
        key = str(idempotency_key or "").strip()
        if not key:
            raise ValueError("idempotency_key is required")
        mode = failure_mode or self.default_failure_mode
        if mode not in FAILURE_MODES:
            raise ValueError(f"unsupported failure mode: {mode}")

        with self._lock:
            self._calls.append(key)
            existing = self._committed.get(key)
            if existing:
                return {
                    "ok": True,
                    "id": existing.provider_object_id,
                    "request_id": existing.request_id,
                    "replayed": True,
                    "sandbox": True,
                }

            if mode == "reject_before_commit":
                return {"ok": False, "error": "sandbox rejection before commit", "sandbox": True}

            receipt = SandboxReceipt(
                idempotency_key=key,
                provider_object_id="sbx_obj_" + uuid.uuid4().hex[:16],
                request_id="sbx_req_" + uuid.uuid4().hex[:16],
            )
            self._committed[key] = receipt

        if mode == "timeout_after_commit":
            # Simulate a response/transport timeout after the remote side effect.
            time.sleep(0.01)
            raise TimeoutError("sandbox response lost after external commit")
        if mode == "crash_after_commit":
            raise RuntimeError("sandbox worker crashed after external commit")

        return {
            "ok": True,
            "id": receipt.provider_object_id,
            "request_id": receipt.request_id,
            "replayed": False,
            "sandbox": True,
        }

    def isolation_evidence(self) -> dict[str, Any]:
        return {"isolated": True, "production_access": False, "tenant": "synthetic-v67"}

    def idempotency_evidence(self) -> dict[str, Any]:
        return {"atomic": True, "key_scoped": True, "duplicate_creates": False}

    def termination_evidence(self) -> dict[str, Any]:
        return {"worker_termination_supported": True, "side_effect_after_kill_testable": True}

    def reconcile(self, *, idempotency_key: str, correlation_id: str | None = None) -> dict[str, Any]:
        key = str(idempotency_key or "").strip()
        with self._lock:
            receipt = self._committed.get(key)
            if not receipt:
                return {"status": "not_found", "idempotency_key": key, "sandbox": True}
            return {
                "status": "committed",
                "idempotency_key": key,
                "provider_object_id": receipt.provider_object_id,
                "provider_request_id": receipt.request_id,
                "sandbox": True,
            }
