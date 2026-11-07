"""Bounded coordinator that turns workspace perception into organism state.

The autonomous OODA loop may invoke this after draining its event queue. It
only synthesizes per-workspace state; it does not propose or execute actions.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("kanyoza.organism.coordinator")


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
    No goal or proposed action is supplied, so this path cannot request
    authorization or cause an external side effect.
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
