"""
Canonical RequestContext — Species Law.

Every authenticated request must resolve to this structure before
business logic runs. Workspace identity is never taken from the
client body alone.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.authorization import normalize_role


@dataclass(frozen=True)
class RequestContext:
    """Immutable per-request identity + tenancy + authority."""

    principal_id: str
    workspace_id: str
    role: str = "viewer"  # viewer | operator | admin | owner | species
    permissions: tuple = ()
    species_privileges: bool = False
    platform_role: str = ""  # super_admin | operator; human-only grant
    platform_permissions: tuple = ()
    principal_type: str = "human"  # human | machine
    auth_method: str = ""
    credential_purpose: str = "workspace_api"
    resource_constraints: Dict[str, Any] = field(default_factory=dict)
    correlation_id: str = ""
    brand_id: Optional[str] = None
    user_email: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def has_permission(self, permission: str) -> bool:
        # Platform permissions are separate from workspace RBAC and human-only.
        is_human_platform_grant = (
            self.principal_type == "human"
            and self.auth_method == "supabase_jwt"
            and self.platform_role == "super_admin"
            and not self.species_privileges
        )
        if permission.startswith("platform:"):
            if (
                self.principal_type != "human"
                or self.auth_method != "supabase_jwt"
                or self.species_privileges
            ):
                return False
            if is_human_platform_grant:
                return True
            return permission in self.platform_permissions
        if is_human_platform_grant:
            return True
        if self.species_privileges or self.role in ("owner", "admin", "species"):
            return True
        return permission in self.permissions

    def assert_workspace(self, target_workspace_id: str) -> None:
        if self.species_privileges or (
            self.principal_type == "human"
            and self.auth_method == "supabase_jwt"
            and self.platform_role == "super_admin"
        ):
            return
        if self.workspace_id != target_workspace_id:
            raise PermissionError(
                f"Principal {self.principal_id} cannot access workspace {target_workspace_id}"
            )

    def effective_workspace(self, client_hint: Optional[str] = None) -> str:
        """
        Resolve workspace for this request.
        Ordinary principals: always self.workspace_id (hint ignored).
        Species operators: may adopt client_hint when provided.
        """
        is_human_platform_grant = (
            self.principal_type == "human"
            and self.auth_method == "supabase_jwt"
            and self.platform_role == "super_admin"
            and not self.species_privileges
        )
        if (self.species_privileges or is_human_platform_grant) and client_hint:
            return client_hint
        return self.workspace_id


_request_ctx: ContextVar[Optional[RequestContext]] = ContextVar(
    "kanyoza_request_context", default=None
)


def set_request_context(ctx: RequestContext) -> None:
    _request_ctx.set(ctx)


def clear_request_context() -> None:
    _request_ctx.set(None)


def get_request_context() -> Optional[RequestContext]:
    return _request_ctx.get()


def require_request_context() -> RequestContext:
    ctx = _request_ctx.get()
    if ctx is None:
        raise RuntimeError("RequestContext not bound — authentication middleware missing")
    return ctx


def bind_from_auth_identity(identity: Any, *, correlation_id: str = "", brand_id: Optional[str] = None) -> RequestContext:
    """
    Build RequestContext from Species AuthIdentity (or compatible object).
    """
    is_species = bool(getattr(identity, "is_species", False))
    workspace_id = getattr(identity, "workspace_id", None)
    if is_species:
        workspace_id = workspace_id or "species"
    else:
        workspace_id = (str(workspace_id).strip() if workspace_id else "")
        if not workspace_id:
            raise PermissionError(
                "Authenticated non-species identity has no workspace_id"
            )
    permissions = tuple(getattr(identity, "permissions", None) or [])
    explicit_role = str(getattr(identity, "role", "") or "").lower()
    if is_species:
        role = "species"
    elif explicit_role:
        role = normalize_role(explicit_role)
    elif "owner" in permissions:
        role = "owner"
    elif "admin" in permissions:
        role = "admin"
    elif "write" in permissions:
        role = "operator"
    else:
        role = "viewer"
    principal = (
        getattr(identity, "user_id", None)
        or getattr(identity, "user_email", None)
        or getattr(identity, "name", None)
        or ("species" if is_species else "anonymous")
    )
    ctx = RequestContext(
        principal_id=str(principal),
        workspace_id=str(workspace_id),
        role=role,
        permissions=permissions,
        species_privileges=is_species,
        platform_role=(
            str(getattr(identity, "platform_role", "") or "")
            if not is_species and str(getattr(identity, "method", "") or "") == "supabase_jwt"
            else ""
        ),
        platform_permissions=(
            tuple(getattr(identity, "platform_permissions", None) or [])
            if not is_species and str(getattr(identity, "method", "") or "") == "supabase_jwt"
            else ()
        ),
        principal_type="machine" if (
            is_species or str(getattr(identity, "method", "") or "") in ("workspace_token", "user_api_key")
        ) else "human",
        auth_method=str(getattr(identity, "method", "") or ""),
        credential_purpose=str(getattr(identity, "credential_purpose", "workspace_api") or "workspace_api"),
        resource_constraints=dict(getattr(identity, "resource_constraints", None) or {}),
        correlation_id=correlation_id or "",
        brand_id=brand_id,
        user_email=getattr(identity, "user_email", None),
    )
    set_request_context(ctx)
    return ctx
