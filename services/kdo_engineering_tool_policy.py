"""Fail-closed tool permission checks for engineering agents.

This is a narrow policy guard, not a replacement for Council or Action Kernel.
Production wiring must obtain grants from the authoritative policy service.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Mapping, Any


class ToolPermissionDenied(PermissionError):
    pass


@dataclass(frozen=True)
class ToolGrant:
    tenant_id: str
    workspace_id: str
    task_id: str
    agent_id: str
    tool_name: str
    decision_id: str


class EngineeringToolPolicy:
    def __init__(self, authorize: Callable[[str, Mapping[str, Any]], Any] | None = None):
        self._authorize = authorize

    def require(self, *, action: str, task: Mapping[str, Any], agent_id: str,
                tool_name: str, decision_id: str | None = None) -> ToolGrant:
        required = ("tenant_id", "workspace_id", "task_id")
        if any(not isinstance(task.get(k), str) or not task[k].strip() for k in required):
            raise ToolPermissionDenied("complete_task_scope_required")
        if not all(isinstance(v, str) and v.strip() for v in (action, agent_id, tool_name)):
            raise ToolPermissionDenied("action_agent_and_tool_required")
        if self._authorize is None:
            raise ToolPermissionDenied("authoritative_authorizer_not_configured")
        try:
            allowed = self._authorize(action, task)
        except Exception as exc:
            raise ToolPermissionDenied("authorization_check_failed") from exc
        if allowed is not True:
            raise ToolPermissionDenied("tool_action_not_authorized")
        # decision_id must come from a trusted policy result in real wiring;
        # a caller-supplied string alone is never treated as proof of approval.
        if not isinstance(decision_id, str) or not decision_id.strip():
            raise ToolPermissionDenied("authoritative_decision_id_required")
        return ToolGrant(task["tenant_id"], task["workspace_id"], task["task_id"],
                         agent_id, tool_name, decision_id)
