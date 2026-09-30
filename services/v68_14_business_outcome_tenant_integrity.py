"""V68.14 — tenant-bound business outcome integrity.

Business outcomes are evidence, not authority.  This module adds a strict,
reusable boundary for the outcome plane: an observation may only reference an
execution from the same workspace, and an attribution candidate may only be
considered when its workspace agrees with the observation.
"""
from __future__ import annotations

from typing import Any, Iterable


def assert_observation_execution_workspace(
    observation: dict[str, Any], execution: Any,
) -> None:
    """Fail closed when an outcome observation crosses execution tenants."""
    if not isinstance(observation, dict):
        raise ValueError("business outcome observation must be an object")
    obs_ws = str(observation.get("workspace_id") or "").strip()
    exec_ws = str(getattr(execution, "workspace_id", None) or (execution.get("workspace_id") if isinstance(execution, dict) else "")).strip()
    obs_exec = str(observation.get("execution_id") or "").strip()
    exec_id = str(getattr(execution, "execution_id", None) or (execution.get("execution_id") if isinstance(execution, dict) else "")).strip()
    if not obs_ws or not exec_ws:
        raise ValueError("workspace_id is required for outcome/execution integrity")
    if not obs_exec or not exec_id:
        raise ValueError("execution_id is required for outcome/execution integrity")
    if obs_exec != exec_id:
        raise ValueError("outcome execution_id does not match execution record")
    if obs_ws != exec_ws:
        raise ValueError("outcome execution workspace mismatch")


def filter_same_workspace_candidates(
    observation: dict[str, Any], candidates: Iterable[Any],
) -> list[Any]:
    """Return only candidates that are explicitly tenant-bound to the observation."""
    ws = str(observation.get("workspace_id") or "").strip()
    if not ws:
        raise ValueError("workspace_id is required")
    selected = []
    for candidate in candidates:
        cws = str(getattr(candidate, "workspace_id", None) or (candidate.get("workspace_id") if isinstance(candidate, dict) else "")).strip()
        if not cws:
            continue
        if cws == ws:
            selected.append(candidate)
    return selected


def assert_attribution_tenant_integrity(attribution: dict[str, Any], observation: dict[str, Any]) -> None:
    """Ensure a persisted attribution cannot cross the observation tenant."""
    if not isinstance(attribution, dict) or not isinstance(observation, dict):
        raise ValueError("attribution and observation must be objects")
    aws = str(attribution.get("workspace_id") or "").strip()
    ows = str(observation.get("workspace_id") or "").strip()
    if not aws or not ows:
        raise ValueError("workspace_id is required for attribution integrity")
    if aws != ows:
        raise ValueError("attribution workspace mismatch")
    if attribution.get("observation_id") != observation.get("observation_id"):
        raise ValueError("attribution observation mismatch")
