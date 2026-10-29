"""Read-only staging preflight for the engineering execution receipt contract.

This module never executes actions or changes database state. It checks that the
configured database role can query the canonical receipt columns needed by the
Phase 13 verifier. It does not prove row-level visibility for every tenant or
replace the authoritative receipt verifier.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


_REQUIRED_COLUMNS = {
    "action_outbox": (
        "workspace_id,intent_id,action_type,decision_id,execution_id,status,parameters"
    ),
    "external_execution_ledger": (
        "workspace_id,execution_id,action,status,provider_object_id,evidence"
    ),
}


def preflight_staging_receipt_schema(db: Any) -> dict[str, Any]:
    """Check read/query access to canonical receipt columns without mutations.

    Uses zero-row SELECTs, so no receipt data is returned or exposed. Database
    clients that do not support a zero-row limit fail closed with a blocker.
    """
    checks: dict[str, dict[str, Any]] = {}
    if db is None or not callable(getattr(db, "table", None)):
        return {
            "ready": False,
            "read_only": True,
            "checks": {},
            "blockers": ["authoritative_database_client_required"],
        }

    for table, columns in _REQUIRED_COLUMNS.items():
        try:
            query = db.table(table).select(columns)
            limit = getattr(query, "limit", None)
            if not callable(limit):
                raise RuntimeError("database_client_missing_zero_row_limit")
            response = limit(0).execute()
            error = getattr(response, "error", None)
            if error:
                raise RuntimeError("database_query_returned_error")
            checks[table] = {"ok": True, "columns_checked": len(columns.split(","))}
        except Exception as exc:
            # Avoid leaking database errors, credentials, or SQL details.
            checks[table] = {"ok": False, "error_type": type(exc).__name__}

    blockers = [f"{table}_receipt_schema_or_read_access_failed"
                for table, result in checks.items() if not result.get("ok")]
    return {
        "ready": not blockers and len(checks) == len(_REQUIRED_COLUMNS),
        "read_only": True,
        "checks": checks,
        "blockers": blockers,
        "limitations": [
            "zero_row_select_does_not_prove_tenant_row_visibility",
            "live_council_and_action_kernel_lifecycle_not_executed",
        ],
    }


def validate_staging_scope(scope: Mapping[str, Any]) -> tuple[bool, list[str]]:
    """Validate required identifiers before a separately authorized staging run."""
    required = ("tenant_id", "workspace_id", "task_id", "decision_id", "action_type")
    missing = [key for key in required
               if not isinstance(scope.get(key), str) or not scope[key].strip()]
    return not missing, [f"{key}_required" for key in missing]
