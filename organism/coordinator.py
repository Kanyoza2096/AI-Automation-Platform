"""Bounded coordinator that turns workspace perception into organism state.

The coordinator may derive narrowly-scoped, evidence-backed goal suggestions
from fresh trusted health events. It never activates goals or executes actions.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger("kanyoza.organism.coordinator")

# Old events restored from the ledger are context, not fresh triggers for a new
# goal proposal. Only recently observed health events can create suggestions.
GOAL_EVENT_MAX_AGE = timedelta(minutes=5)  # bounded freshness window for new suggestions


@dataclass(frozen=True)
class CycleBatch:
    attempted: int
    completed: int
    failed: int
    workspace_ids: tuple[str, ...] = ()


async def cycle_started_organisms(
    registry: Any,
    *,
    max_workspaces: int = 32,
) -> CycleBatch:
    """Run one observation-to-state cycle for each already-started organism.

    The coordinator never creates or starts workspaces. A failure in one
    workspace is isolated and cannot prevent other tenants from being cycled.
    Goal proposals remain suggest-only and require the runtime's already-bound
    server-side persistence client; this function does not discover credentials.
    """
    if max_workspaces < 1:
        raise ValueError("max_workspaces must be positive")

    from organism.lifecycle import cycle_async

    workspace_ids = tuple(registry.list_ids()[:max_workspaces])
    completed_ids: list[str] = []
    failed = 0
    attempted = 0

    for workspace_id in workspace_ids:
        organism = registry.get(workspace_id)
        if organism is None or not getattr(organism, "_running", False):
            continue
        runtime = getattr(organism, "runtime", None)
        if runtime is None or runtime.workspace_id != workspace_id:
            failed += 1
            logger.warning("Organism cycle skipped: runtime boundary unavailable workspace=%s", workspace_id)
            continue
        attempted += 1
        try:
            await cycle_async(runtime)
            await _persist_fresh_goal_proposals(runtime, workspace_id)
            await _restore_pending_goal_proposals(runtime, workspace_id)
            store = getattr(runtime, "persistence_store", None)
            if store is not None:
                await store.persist(runtime)
            completed_ids.append(workspace_id)
        except Exception:
            failed += 1
            logger.exception("Organism state synthesis failed workspace=%s", workspace_id)

    return CycleBatch(
        attempted=attempted,
        completed=len(completed_ids),
        failed=failed,
        workspace_ids=tuple(completed_ids),
    )


async def _persist_fresh_goal_proposals(runtime: Any, workspace_id: str) -> int:
    """Persist fresh trusted health proposals; retain failed writes for retry."""
    from organism.goals import derive_goal_proposal

    if runtime.workspace_id != workspace_id:
        raise RuntimeError("goal proposal workspace identity mismatch")

    processed = getattr(runtime, "_goal_proposal_observation_ids", None)
    if processed is None:
        processed = set()
        runtime._goal_proposal_observation_ids = processed
    pending = getattr(runtime, "_pending_goal_proposals", None)
    if pending is None:
        pending = {}
        runtime._pending_goal_proposals = pending

    # Derive only from recent events. A pending proposal survives a transient
    # storage outage, but its own expiry bounds how long it can be retried.
    for observation in _fresh_health_observations(runtime, workspace_id):
        observation_id = observation.observation_id
        if observation_id in processed or observation_id in pending:
            continue
        proposal = derive_goal_proposal(observation)
        if proposal is None:
            processed.add(observation_id)
            continue
        if proposal.workspace_id != workspace_id:
            raise RuntimeError("derived goal proposal workspace mismatch")
        pending[observation_id] = proposal

    now = datetime.now(timezone.utc)
    for observation_id, proposal in tuple(pending.items()):
        if proposal.workspace_id != workspace_id:
            raise RuntimeError("pending goal proposal workspace mismatch")
        if proposal.expires_at <= now:
            pending.pop(observation_id, None)
            processed.add(observation_id)

    persistence = getattr(runtime, "persistence_store", None)
    has_client = persistence is not None and getattr(persistence, "client", None) is not None
    if pending and not has_client:
        raise RuntimeError("goal proposal store unavailable: runtime persistence client is not bound")

    store = getattr(runtime, "goal_proposal_store", None)
    if store is None and has_client:
        from organism.goal_persistence import GoalProposalStore
        store = GoalProposalStore(persistence.client)
        runtime.goal_proposal_store = store

    if not pending:
        return 0

    persisted = 0
    for observation_id, proposal in tuple(pending.items()):
        # Mark processed only after the store acknowledges persistence. A
        # transient failure retains the proposal for retry; deterministic IDs
        # make a retry safe even if the DB committed before the response failed.
        persisted += await store.persist(proposal)
        pending.pop(observation_id, None)
        processed.add(observation_id)
    return persisted

def _fresh_health_observations(runtime: Any, workspace_id: str):
    from organism.perception import ObservationKind

    now = datetime.now(timezone.utc)
    return tuple(
        item for item in runtime.perception.recent(workspace_id, kind=ObservationKind.HEALTH)
        if item.workspace_id == workspace_id
        and item.observed_at <= now + timedelta(seconds=30)
        and now - item.observed_at <= GOAL_EVENT_MAX_AGE
    )


async def _restore_pending_goal_proposals(runtime: Any, workspace_id: str) -> int:
    """Attach durable unexpired suggestions to the same workspace runtime."""
    if runtime.workspace_id != workspace_id:
        raise RuntimeError("goal proposal recovery workspace identity mismatch")
    store = getattr(runtime, "goal_proposal_store", None)
    if store is None:
        runtime.pending_goal_proposals = ()
        return 0
    list_pending = getattr(store, "list_pending", None)
    if not callable(list_pending):
        raise RuntimeError("goal proposal store does not support pending recovery")
    proposals = await list_pending(workspace_id)
    if any(
        proposal.workspace_id != workspace_id
        or proposal.authority != "suggest"
        or proposal.status != "proposed"
        for proposal in proposals
    ):
        raise RuntimeError("recovered goal proposal violates workspace or authority boundary")
    runtime.pending_goal_proposals = tuple(proposals)
    return len(proposals)
