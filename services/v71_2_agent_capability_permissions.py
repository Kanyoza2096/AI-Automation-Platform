"""V71.2 — Governed AI Workforce: Agent Capability Permission Plane.

Identity says who an agent is. This module says which declared capabilities that
identity is permitted to request. It never grants business authority, approval,
provider access, or execution rights. The existing policy/approval/Action Kernel
planes remain authoritative for consequential work.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional
import hashlib, json

from services.v71_1_agent_identity import AgentIdentityRegistry, AgentStatus


class PermissionEffect(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


class PermissionBlock(str, Enum):
    TENANT_MISMATCH = "tenant_mismatch"
    AGENT_NOT_FOUND = "agent_not_found"
    AGENT_NOT_ACTIVE = "agent_not_active"
    CAPABILITY_NOT_DECLARED = "capability_not_declared"
    PERMISSION_NOT_FOUND = "permission_not_found"
    PERMISSION_DENIED = "permission_denied"
    SCOPE_MISMATCH = "scope_mismatch"
    INVALID_PERMISSION = "invalid_permission"
    DUPLICATE_PERMISSION = "duplicate_permission"
    TERMINAL = "agent_retired"
    PERMISSION_TAMPERED = "permission_tampered"


@dataclass(frozen=True)
class AgentCapabilityPermission:
    permission_id: str
    workspace_id: str
    agent_id: str
    capability: str
    effect: PermissionEffect = PermissionEffect.DENY
    scope: Mapping[str, Any] = field(default_factory=dict)
    version: str = "1"
    permission_fingerprint: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "permission_id": self.permission_id,
            "workspace_id": self.workspace_id,
            "agent_id": self.agent_id,
            "capability": self.capability,
            "effect": self.effect.value,
            "scope": dict(self.scope),
            "version": self.version,
        }

    def fingerprint(self) -> str:
        raw = json.dumps(self.canonical_payload(), sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()


class AgentCapabilityPermissionRegistry:
    VERSION = "v71.2"

    def __init__(self, workspace_id: str, identity_registry: AgentIdentityRegistry):
        ws = str(workspace_id or "").strip()
        if not ws or identity_registry.workspace_id != ws:
            raise ValueError(PermissionBlock.TENANT_MISMATCH.value)
        self.workspace_id = ws
        self.identity_registry = identity_registry
        self._permissions: dict[str, AgentCapabilityPermission] = {}

    def grant(self, *, permission_id: str, agent_id: str, capability: str,
              scope: Optional[Mapping[str, Any]] = None, version: str = "1") -> AgentCapabilityPermission:
        pid, cap = str(permission_id or "").strip(), str(capability or "").strip()
        if not pid or not cap or not str(version or "").strip():
            raise ValueError(PermissionBlock.INVALID_PERMISSION.value)
        if pid in self._permissions:
            raise ValueError(PermissionBlock.DUPLICATE_PERMISSION.value)
        agent = self.identity_registry.get(agent_id)
        if agent is None:
            raise ValueError(PermissionBlock.AGENT_NOT_FOUND.value)
        self.identity_registry.assert_integrity(agent)
        if agent.status == AgentStatus.RETIRED:
            raise ValueError(PermissionBlock.TERMINAL.value)
        if cap not in agent.declared_capabilities:
            raise ValueError(PermissionBlock.CAPABILITY_NOT_DECLARED.value)
        permission = AgentCapabilityPermission(pid, self.workspace_id, str(agent_id), cap,
                                               PermissionEffect.ALLOW, dict(scope or {}), str(version), "")
        permission = AgentCapabilityPermission(**{**permission.__dict__, "permission_fingerprint": permission.fingerprint()})
        self._permissions[pid] = permission
        return permission

    def deny(self, *, permission_id: str, agent_id: str, capability: str,
             scope: Optional[Mapping[str, Any]] = None, version: str = "1") -> AgentCapabilityPermission:
        pid, cap = str(permission_id or "").strip(), str(capability or "").strip()
        if not pid or not cap or not str(version or "").strip():
            raise ValueError(PermissionBlock.INVALID_PERMISSION.value)
        if pid in self._permissions:
            raise ValueError(PermissionBlock.DUPLICATE_PERMISSION.value)
        agent = self.identity_registry.get(agent_id)
        if agent is None:
            raise ValueError(PermissionBlock.AGENT_NOT_FOUND.value)
        self.identity_registry.assert_integrity(agent)
        if cap not in agent.declared_capabilities:
            raise ValueError(PermissionBlock.CAPABILITY_NOT_DECLARED.value)
        permission = AgentCapabilityPermission(pid, self.workspace_id, str(agent_id), cap,
                                               PermissionEffect.DENY, dict(scope or {}), str(version), "")
        permission = AgentCapabilityPermission(**{**permission.__dict__, "permission_fingerprint": permission.fingerprint()})
        self._permissions[pid] = permission
        return permission

    def get(self, permission_id: str) -> Optional[AgentCapabilityPermission]:
        return self._permissions.get(str(permission_id))

    def assert_integrity(self, permission: AgentCapabilityPermission) -> None:
        if permission.workspace_id != self.workspace_id:
            raise ValueError(PermissionBlock.TENANT_MISMATCH.value)
        if permission.permission_fingerprint != permission.fingerprint():
            raise ValueError(PermissionBlock.PERMISSION_TAMPERED.value)
        agent = self.identity_registry.get(permission.agent_id)
        if agent is None:
            raise ValueError(PermissionBlock.AGENT_NOT_FOUND.value)
        self.identity_registry.assert_integrity(agent)
        if permission.capability not in agent.declared_capabilities:
            raise ValueError(PermissionBlock.CAPABILITY_NOT_DECLARED.value)

    @staticmethod
    def _scope_matches(required: Mapping[str, Any], requested: Mapping[str, Any]) -> bool:
        # Permission scope is an allowlist. Every declared key must match exactly
        # (lists are treated as membership allowlists); an empty scope is global
        # within this permission but still bounded by identity + governance.
        for key, allowed in required.items():
            if key not in requested:
                return False
            value = requested[key]
            if isinstance(allowed, (tuple, list, set, frozenset)):
                if value not in set(allowed):
                    return False
            elif value != allowed:
                return False
        return True

    def check(self, *, agent_id: str, capability: str,
              requested_scope: Optional[Mapping[str, Any]] = None) -> tuple[bool, tuple[str, ...]]:
        agent = self.identity_registry.get(agent_id)
        if agent is None:
            return False, (PermissionBlock.AGENT_NOT_FOUND.value,)
        try:
            self.identity_registry.require_active(agent_id)
        except ValueError as exc:
            return False, (str(exc),)
        cap = str(capability or "").strip()
        if cap not in agent.declared_capabilities:
            return False, (PermissionBlock.CAPABILITY_NOT_DECLARED.value,)
        requested = dict(requested_scope or {})
        candidates = [p for p in self._permissions.values() if p.agent_id == agent_id and p.capability == cap]
        if not candidates:
            return False, (PermissionBlock.PERMISSION_NOT_FOUND.value,)
        # Explicit deny wins over allow. This prevents a broad allow from
        # accidentally overriding a narrower prohibition.
        for permission in candidates:
            self.assert_integrity(permission)
            if permission.effect == PermissionEffect.DENY and self._scope_matches(permission.scope, requested):
                return False, (PermissionBlock.PERMISSION_DENIED.value,)
        for permission in candidates:
            if permission.effect == PermissionEffect.ALLOW and self._scope_matches(permission.scope, requested):
                return True, ()
        return False, (PermissionBlock.SCOPE_MISMATCH.value,)

    def can_request(self, *, agent_id: str, capability: str,
                    requested_scope: Optional[Mapping[str, Any]] = None) -> bool:
        allowed, _ = self.check(agent_id=agent_id, capability=capability, requested_scope=requested_scope)
        return allowed

    def list_permissions(self, agent_id: Optional[str] = None) -> tuple[AgentCapabilityPermission, ...]:
        values = tuple(self._permissions.values())
        if agent_id is None:
            return values
        return tuple(p for p in values if p.agent_id == str(agent_id))


__all__ = ["PermissionEffect", "PermissionBlock", "AgentCapabilityPermission", "AgentCapabilityPermissionRegistry"]
