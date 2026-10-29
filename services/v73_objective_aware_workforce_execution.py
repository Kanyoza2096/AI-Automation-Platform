"""V73 objective-aware workforce execution contract.

The workforce may execute only a decision that already carries durable objective
lineage. This module validates the lineage and produces an immutable execution
context; it grants no authority and performs no side effects.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

SCHEMA_VERSION = "1.0"


def build_objective_execution_context(*, workspace_id: str, run_id: str,
                                      objective_context: Mapping[str, Any],
                                      success_criteria: Mapping[str, Any] | None = None) -> dict[str, Any]:
    from services.business_objective_context import validate_objective_context
    validate_objective_context(objective_context, workspace_id=workspace_id)
    ws = str(workspace_id or "").strip()
    run = str(run_id or "").strip()
    if not ws or not run:
        raise ValueError("workspace_id and run_id are required")
    criteria = dict(success_criteria or {})
    context = {
        "schema_version": SCHEMA_VERSION,
        "workspace_id": ws,
        "run_id": run,
        "objective_id": str(objective_context["objective_id"]),
        "objective": str(objective_context["objective"]),
        "objective_context_fingerprint": str(objective_context["context_fingerprint"]),
        "success_criteria": criteria,
        "authority": "none",
    }
    raw = json.dumps(context, sort_keys=True, separators=(",", ":"), default=str)
    context["execution_context_fingerprint"] = hashlib.sha256(raw.encode()).hexdigest()
    return context


def validate_objective_execution_context(value: Mapping[str, Any], *, workspace_id: str,
                                          run_id: str | None = None) -> None:
    if not isinstance(value, Mapping) or value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported objective execution context schema")
    if str(value.get("workspace_id") or "") != str(workspace_id or ""):
        raise ValueError("objective execution context workspace mismatch")
    if run_id is not None and str(value.get("run_id") or "") != str(run_id):
        raise ValueError("objective execution context run mismatch")
    for key in ("objective_id", "objective", "objective_context_fingerprint", "execution_context_fingerprint"):
        if not value.get(key):
            raise ValueError(f"{key} is required")
    if value.get("authority") != "none":
        raise ValueError("objective execution context cannot grant authority")
    if not isinstance(value.get("success_criteria"), Mapping):
        raise ValueError("success_criteria must be an object")


def assert_execution_lineage(*, run: Mapping[str, Any], execution_context: Mapping[str, Any]) -> None:
    validate_objective_execution_context(
        execution_context,
        workspace_id=str(run.get("workspace_id") or ""),
        run_id=str(run.get("run_id") or ""),
    )
    if str(run.get("objective_id") or "") != str(execution_context.get("objective_id") or ""):
        raise ValueError("workforce objective lineage mismatch")
