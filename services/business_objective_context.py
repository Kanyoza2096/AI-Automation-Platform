"""IPC-12 — objective-bound business decision context.

Intelligence may explain what is happening, but an executable decision must be
bound to a durable business objective owned by the same workspace. This module
only resolves and binds objective context; it never grants execution authority.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Mapping

SCHEMA_VERSION = "1.0"
ACTIVE_STATE = "active"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _fingerprint(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(dict(value), sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()


def resolve_active_objective_context(workspace_id: str, db: Any) -> dict[str, Any] | None:
    """Resolve exactly one current active objective from the durable objective table.

    No default/demo objective is synthesized. Missing/unavailable objective truth
    is represented as ``None`` so callers can fail closed.
    """
    ws = str(workspace_id or "").strip()
    if not ws or db is None:
        return None
    try:
        result = (
            db.table("business_operating_objectives")
            .select("objective_id,workspace_id,objective,state,envelope_id,updated_at")
            .eq("workspace_id", ws)
            .eq("state", ACTIVE_STATE)
            .order("updated_at", desc=True)
            .limit(1)
            .execute()
        )
    except Exception:
        return None
    rows = list(getattr(result, "data", None) or [])
    if not rows:
        return None
    row = dict(rows[0])
    if str(row.get("workspace_id") or "") != ws or str(row.get("state") or "") != ACTIVE_STATE:
        return None
    objective_id = str(row.get("objective_id") or "").strip()
    objective = str(row.get("objective") or "").strip()
    if not objective_id or not objective:
        return None
    observed_at = _parse_time(row.get("updated_at")) or _now()
    context = {
        "schema_version": SCHEMA_VERSION,
        "objective_id": objective_id,
        "workspace_id": ws,
        "objective": objective,
        "state": ACTIVE_STATE,
        "envelope_id": str(row.get("envelope_id") or "") or None,
        "objective_updated_at": observed_at.isoformat(),
        "observed_at": _now().isoformat(),
        "authority": "none",
    }
    context["context_fingerprint"] = _fingerprint(context)
    return context


def bind_objective_context(candidate: Mapping[str, Any], context: Mapping[str, Any] | None) -> dict[str, Any]:
    """Attach a tenant-matched objective snapshot to a non-authoritative candidate."""
    value = dict(candidate)
    if context is None:
        value["objective_binding_status"] = "missing"
        value["objective_context"] = None
        value["objective_id"] = None
        value.setdefault("constraints", []).append("active_business_objective_required")
        value["status"] = "review_required" if value.get("status") == "proposed" else value.get("status")
        return value

    validate_objective_context(context, workspace_id=str(value.get("workspace_id") or ""))
    value["objective_context"] = dict(context)
    value["objective_id"] = context["objective_id"]
    value["objective_binding_status"] = "bound"
    value["constraints"] = list(value.get("constraints") or [])
    return value


def validate_objective_context(context: Mapping[str, Any], *, workspace_id: str) -> None:
    if not isinstance(context, Mapping) or context.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported objective context schema")
    if str(context.get("workspace_id") or "") != str(workspace_id or ""):
        raise ValueError("objective context workspace mismatch")
    if context.get("state") != ACTIVE_STATE:
        raise ValueError("objective context must reference an active objective")
    for key in ("objective_id", "objective", "context_fingerprint"):
        if not context.get(key):
            raise ValueError(f"{key} is required")
    if context.get("authority") != "none":
        raise ValueError("objective context cannot grant authority")


def require_objective_binding(evaluation: Mapping[str, Any]) -> None:
    """Strict IPC-12 admission check for an executable policy evaluation."""
    disposition = str(evaluation.get("disposition") or "")
    if disposition != "ALLOW":
        return
    context = evaluation.get("objective_context")
    if context is None:
        raise ValueError("executable decision requires an active business objective")
    validate_objective_context(context, workspace_id=str(evaluation.get("workspace_id") or ""))
    if str(evaluation.get("objective_id") or "") != str(context.get("objective_id") or ""):
        raise ValueError("objective binding does not match evaluation")
