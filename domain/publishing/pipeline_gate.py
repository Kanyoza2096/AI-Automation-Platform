"""
Publish authorization gate — single entry before durable jobs.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from core.pipeline import PipelineResult, run_action_pipeline
from core.request_context import RequestContext, get_request_context, set_request_context


def ensure_context(workspace_id: str) -> RequestContext:
    ctx = get_request_context()
    if ctx is not None:
        return ctx
    ctx = RequestContext(
        principal_id="api",
        workspace_id=str(workspace_id),
        role="operator",
        permissions=("read", "write"),
    )
    set_request_context(ctx)
    return ctx


def gate_publish(
    *,
    workspace_id: str,
    payload: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, PipelineResult]:
    """
    Returns (allowed, result).
    When allowed, result.decision_id is a DecisionGate id for workers.
    """
    ctx = ensure_context(workspace_id)
    result = run_action_pipeline(
        action="publish",
        payload=dict(payload or {}),
        permission="write",
        ctx=ctx,
        enqueue=False,
    )
    if result.allowed:
        # Ensure DecisionGate has an entry (pipeline also issues; belt-and-suspenders)
        try:
            from core.decision_gate import DecisionGate, DecisionSource
            if not (result.metadata or {}).get("decision_gate_id"):
                gate = DecisionGate()
                issued = gate.issue(
                    source=DecisionSource.DIRECTOR_AUTO,
                    workspace_id=workspace_id,
                    action_name="publish",
                    approved=True,
                    correlation_id=result.correlation_id,
                    reason=result.reason or "pipeline_allow",
                )
                result.decision_id = issued.decision_id
                result.metadata["decision_gate_id"] = issued.decision_id
        except Exception:
            pass
    return result.allowed, result
