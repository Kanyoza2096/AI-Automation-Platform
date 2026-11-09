"""Durable, tenant-scoped storage for suggest-only organism goal proposals."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from organism.goals import GoalPriority, GoalProposal


class GoalProposalStoreError(RuntimeError):
    """A proposal could not be safely persisted or recovered."""


@dataclass(frozen=True)
class GoalProposalStoreBootstrapResult:
    store: "GoalProposalStore | None"
    status: str


def bootstrap_goal_proposal_store(
    service_role_client: Any,
    workspace_id: str,
) -> GoalProposalStoreBootstrapResult:
    """Create the workspace-scoped store only after read-only schema checks.

    This function never creates or alters schema and never falls back to an
    anon/browser client. It is intended for authenticated organism startup.
    """
    if service_role_client is None:
        return GoalProposalStoreBootstrapResult(None, "disabled_no_service_role_client")
    if not isinstance(workspace_id, str) or not workspace_id.strip():
        return GoalProposalStoreBootstrapResult(None, "disabled_workspace_identity_missing")

    try:
        schema = (
            service_role_client.table("organism_goal_proposals")
            .select("proposal_id,workspace_id,authority,status,expires_at")
            .limit(0)
            .execute()
        )
        if getattr(schema, "data", None) is None:
            return GoalProposalStoreBootstrapResult(None, "disabled_schema_not_ready")
    except Exception:
        return GoalProposalStoreBootstrapResult(None, "disabled_schema_not_ready")

    try:
        workspace = (
            service_role_client.table("workspaces")
            .select("id,status")
            .eq("id", workspace_id)
            .limit(1)
            .execute()
        )
        rows = getattr(workspace, "data", None)
        if not rows:
            return GoalProposalStoreBootstrapResult(None, "disabled_workspace_unavailable")
        row = rows[0]
        if row.get("id") != workspace_id or row.get("status") != "active":
            return GoalProposalStoreBootstrapResult(None, "disabled_workspace_inactive")
    except Exception:
        return GoalProposalStoreBootstrapResult(None, "disabled_workspace_lookup_failed")

    return GoalProposalStoreBootstrapResult(
        GoalProposalStore(service_role_client),
        "ready",
    )


class GoalProposalStore:
    """Persist proposals without granting update or execution authority."""

    def __init__(self, client: Any) -> None:
        if client is None:
            raise ValueError("service-role database client is required")
        self.client = client

    async def persist(self, proposal: GoalProposal) -> int:
        if proposal.authority != "suggest" or proposal.status != "proposed":
            raise ValueError("only proposed suggest-authority goals may be persisted")
        try:
            await asyncio.to_thread(self._validate_workspace_active, proposal.workspace_id)
            record = {
                "proposal_id": proposal.proposal_id,
                "workspace_id": proposal.workspace_id,
                "title": proposal.title,
                "success_condition": proposal.success_condition,
                "source_event_ids": list(proposal.source_event_ids),
                "priority": proposal.priority.value,
                "risk_level": proposal.risk_level,
                "expected_outcome": proposal.expected_outcome,
                "created_at": proposal.created_at.astimezone(timezone.utc).isoformat(),
                "expires_at": proposal.expires_at.astimezone(timezone.utc).isoformat(),
                "authority": "suggest",
                "status": "proposed",
            }
            # Deterministic proposal IDs make retries idempotent. Ignore a
            # duplicate rather than overwriting an existing review/status row.
            await asyncio.to_thread(
                lambda: self.client.table("organism_goal_proposals")
                .upsert(record, on_conflict="proposal_id", ignore_duplicates=True)
                .execute()
            )
            return 1
        except Exception as exc:
            if isinstance(exc, (ValueError, GoalProposalStoreError)):
                raise
            raise GoalProposalStoreError("goal proposal persistence failed") from exc

    async def list_pending(
        self,
        workspace_id: str,
        *,
        now: datetime | None = None,
        limit: int = 50,
    ) -> tuple[GoalProposal, ...]:
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise ValueError("workspace_id is required")
        if not 1 <= int(limit) <= 100:
            raise ValueError("limit must be between 1 and 100")
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        try:
            await asyncio.to_thread(self._validate_workspace_active, workspace_id)
            result = await asyncio.to_thread(
                lambda: self.client.table("organism_goal_proposals")
                .select(
                    "proposal_id,workspace_id,title,success_condition,source_event_ids,"
                    "priority,risk_level,expected_outcome,created_at,expires_at,authority,status"
                )
                .eq("workspace_id", workspace_id)
                .eq("status", "proposed")
                .gt("expires_at", current.astimezone(timezone.utc).isoformat())
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
            )
            proposals = []
            for row in result.data or []:
                if row.get("workspace_id") != workspace_id:
                    raise GoalProposalStoreError("proposal_workspace_mismatch")
                if row.get("authority") != "suggest" or row.get("status") != "proposed":
                    raise GoalProposalStoreError("proposal_authority_or_status_mismatch")
                proposals.append(GoalProposal(
                    proposal_id=row["proposal_id"],
                    workspace_id=row["workspace_id"],
                    title=row["title"],
                    success_condition=row["success_condition"],
                    source_event_ids=tuple(row["source_event_ids"]),
                    priority=GoalPriority(row["priority"]),
                    risk_level=row["risk_level"],
                    expected_outcome=row["expected_outcome"],
                    created_at=_parse_timestamp(row["created_at"]),
                    expires_at=_parse_timestamp(row["expires_at"]),
                    authority="suggest",
                    status="proposed",
                ))
            return tuple(proposals)
        except Exception as exc:
            if isinstance(exc, (ValueError, GoalProposalStoreError)):
                raise
            raise GoalProposalStoreError("goal proposal recovery failed") from exc

    def _validate_workspace_active(self, workspace_id: str) -> None:
        result = (
            self.client.table("workspaces")
            .select("id,status")
            .eq("id", workspace_id)
            .limit(1)
            .execute()
        )
        rows = list(result.data or [])
        if not rows:
            raise GoalProposalStoreError("unknown_workspace")
        if str(rows[0].get("status") or "").strip().lower() != "active":
            raise GoalProposalStoreError("inactive_workspace")


def _parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise GoalProposalStoreError("proposal_timestamp_missing_timezone")
    return parsed.astimezone(timezone.utc)


__all__ = ["GoalProposalStore", "GoalProposalStoreError"]


__all__ = [
    "GoalProposalStoreError",
    "GoalProposalStore",
    "GoalProposalStoreBootstrapResult",
    "bootstrap_goal_proposal_store",
]
