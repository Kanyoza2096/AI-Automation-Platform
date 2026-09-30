"""V65.8 durable audit and idempotency boundary for decision evaluations.

A policy evaluation is an auditable fact. Re-delivery of the same candidate under
an unchanged policy must return the original evaluation rather than minting a
second decision fact. A conflicting payload under the same idempotency key fails
closed. This layer grants no authority and performs no side effects itself.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from typing import Any

logger = logging.getLogger("kanyoza.business_decision_audit")
SCHEMA_VERSION = "1.0"


def make_idempotency_key(evaluation: dict[str, Any]) -> str:
    for key in ("candidate_id", "workspace_id", "policy_version"):
        if not evaluation.get(key):
            raise ValueError(f"{key} is required for decision idempotency")
    material = "|".join(str(evaluation[k]).strip() for k in ("workspace_id", "candidate_id", "policy_version"))
    return "dpe_" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def fingerprint(evaluation: dict[str, Any]) -> str:
    material = json.dumps(evaluation, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def build_audit_record(evaluation: dict[str, Any]) -> dict[str, Any]:
    from services.business_decision_policy import validate_decision_policy_evaluation
    validate_decision_policy_evaluation(evaluation)
    record = {
        "schema_version": SCHEMA_VERSION,
        "audit_id": "dpa_" + uuid.uuid4().hex,
        "evaluation_id": str(evaluation["evaluation_id"]),
        "candidate_id": str(evaluation["candidate_id"]),
        "understanding_id": str(evaluation["understanding_id"]),
        "workspace_id": str(evaluation["workspace_id"]),
        "idempotency_key": make_idempotency_key(evaluation),
        "evaluation_fingerprint": fingerprint(evaluation),
        "policy_version": str(evaluation["policy_version"]),
        "disposition": str(evaluation["disposition"]),
        "authority": "none",
    }
    validate_audit_record(record)
    return record


def validate_audit_record(value: dict[str, Any]) -> None:
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported decision audit schema")
    for key in ("audit_id", "evaluation_id", "candidate_id", "understanding_id", "workspace_id", "idempotency_key", "evaluation_fingerprint", "policy_version", "disposition"):
        if not value.get(key):
            raise ValueError(f"{key} is required")
    if value.get("authority") != "none":
        raise ValueError("decision audit cannot grant authority")


class DecisionAuditLedger:
    """DB-authoritative audit ledger with a safe in-memory test fallback."""

    def __init__(self, db=None, *, allow_in_memory: bool = True):
        self._db = db
        self._allow_in_memory = allow_in_memory
        self._records: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}

    def record(self, evaluation: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        audit = build_audit_record(evaluation)
        key = audit["idempotency_key"]
        fp = audit["evaluation_fingerprint"]
        if self._db is not None:
            try:
                result = self._db.table("business_decision_audit").upsert(
                    {**audit, "evaluation": evaluation},
                    on_conflict="idempotency_key",
                    ignore_duplicates=True,
                ).execute()
                # Re-read the authoritative row to distinguish first write from replay.
                row_result = self._db.table("business_decision_audit").select("*").eq("idempotency_key", key).limit(1).execute()
                rows = list(getattr(row_result, "data", None) or [])
                if not rows:
                    raise RuntimeError("decision audit write produced no authoritative row")
                row = rows[0]
                if str(row.get("evaluation_fingerprint")) != fp:
                    raise RuntimeError("decision idempotency conflict: same key has different evaluation")
                stored = dict(row.get("evaluation") or evaluation)
                return stored, str(row.get("evaluation_fingerprint")) == fp and str(row.get("audit_id")) != audit["audit_id"]
            except Exception:
                # Never silently fall back to memory when a DB is configured.
                raise
        if not self._allow_in_memory:
            raise RuntimeError("durable decision audit database is required")
        existing = self._records.get(key)
        if existing:
            stored_audit, stored_eval = existing
            if stored_audit["evaluation_fingerprint"] != fp:
                raise RuntimeError("decision idempotency conflict: same key has different evaluation")
            return stored_eval, True
        self._records[key] = (audit, dict(evaluation))
        return dict(evaluation), False
