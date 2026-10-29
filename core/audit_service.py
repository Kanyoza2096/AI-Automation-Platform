"""
Canonical AuditService — one write path, one table: audit_logs.

All security-sensitive operations should call AuditService.log(...).
core.auth.AuditLogger is a compatibility alias.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger("kanyoza.core.audit_service")

AUDIT_TABLE = "audit_logs"


class AuditService:
    """Persists audit events to the canonical ``audit_logs`` table."""

    def __init__(self, supabase_client=None) -> None:
        self._db = supabase_client

    def set_supabase_client(self, client) -> None:
        self._db = client

    def log(
        self,
        *,
        action: str,
        resource: str = "",
        resource_id: str = "",
        actor: str = "system",
        workspace_id: Optional[str] = None,
        result: str = "success",
        correlation_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        entry = {
            "action": action,
            "resource": resource,
            "resource_id": resource_id or None,
            "actor": actor,
            "workspace_id": workspace_id,
            "result": result,
            "correlation_id": correlation_id,
            "metadata": metadata or {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if not self._db:
            logger.info("audit (no db): %s %s ws=%s", action, resource, workspace_id)
            return
        try:
            self._db.table(AUDIT_TABLE).insert(entry).execute()
        except Exception as exc:
            # Fallback: minimal columns if schema differs
            logger.warning("audit_logs insert failed (%s) — retrying minimal", exc)
            try:
                self._db.table(AUDIT_TABLE).insert({
                    "action": action,
                    "resource": resource,
                    "actor": actor,
                    "created_at": entry["created_at"],
                }).execute()
            except Exception as exc2:
                logger.error("audit_logs insert failed: %s", exc2)

    # Compatibility with old AuditLogger.log_action signature
    def log_action(
        self,
        action: str,
        resource: str = "",
        user: str = "system",
        workspace_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        self.log(
            action=action,
            resource=resource,
            actor=user,
            workspace_id=workspace_id,
            metadata=details or kwargs or {},
        )


# Singleton used by dependency_registry / adapters
audit_service = AuditService()
