"""
Phase A — Species Auth (single door).

Canonical authentication for the Kanyoza organism platform.

ALLOWED credentials (only):
  1. MASTER_API_TOKEN       → platform operator (species-level, all workspaces)
  2. PYTHON_SERVICE_TOKEN   → KCPG Gateway service credential (species-level)
  3. KDO_API_ API keys       → workspace-scoped (workspace_id from token record)
     Legacy kny_ / kz_ tokens remain accepted during controlled rotation.
  4. Supabase JWT           → workspace resolved from owner_email / membership

REMOVED (never accept again):
  - APP_SECRET as API key
  - Owner PSID as API key
  - ?token= query-string credentials

Workspace identity always comes from the credential, never from
client-supplied X-Workspace-ID alone.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, List, Optional

from core.authorization import normalize_role

logger = logging.getLogger("kanyoza.core.species_auth")

def _configured_user_ids(raw: str) -> set[str]:
    """Parse server-only UUID allowlists; no email or client metadata matching."""
    valid = set()
    for item in str(raw or "").split(","):
        candidate = item.strip().lower()
        if not candidate:
            continue
        try:
            valid.add(str(uuid.UUID(candidate)))
        except (ValueError, AttributeError, TypeError):
            logger.warning("Ignoring invalid platform authority UUID entry")
    return valid


def resolve_platform_authority(user_id: str, *, super_admin_ids: str = "", operator_ids: str = "") -> tuple[str, list[str]]:
    """Resolve a human platform grant from an explicit server-side UUID allowlist."""
    subject = str(user_id or "").strip().lower()
    if not subject:
        return "", []
    if subject in _configured_user_ids(super_admin_ids):
        return "super_admin", ["platform:*"]
    if subject in _configured_user_ids(operator_ids):
        return "operator", ["platform:health:read", "platform:workspaces:read", "platform:operations:read"]
    return "", []



# Permissions a master token holds
MASTER_PERMISSIONS = ["read", "write", "admin", "owner", "species"]


def _workspace_token_role(permissions: List[str]) -> str:
    """Derive the workspace role from the token's explicit permission scope."""
    granted = {str(permission).strip().lower() for permission in permissions}
    if "owner" in granted:
        return "owner"
    if "admin" in granted:
        return "admin"
    if "operator" in granted or "write" in granted:
        return "operator"
    return "viewer"


@dataclass
class AuthIdentity:
    """Resolved identity after successful authentication."""

    valid: bool
    method: str = ""  # master | python_service | supabase_jwt | workspace_token | user_api_key
    workspace_id: Optional[str] = None  # None = species/master (all workspaces)
    role: str = "viewer"
    user_email: Optional[str] = None
    user_id: Optional[str] = None
    permissions: List[str] = field(default_factory=list)
    name: str = ""
    is_species: bool = False  # True for master token or python service token
    platform_role: str = ""  # Separate human platform authority; never inferred from workspace role
    platform_permissions: List[str] = field(default_factory=list)
    credential_purpose: str = "workspace_api"
    resource_constraints: dict = field(default_factory=dict)
    error: str = ""

    def has_permission(self, permission: str) -> bool:
        is_human_platform_grant = (
            not self.is_species
            and self.method == "supabase_jwt"
            and self.platform_role == "super_admin"
        )
        if permission.startswith("platform:"):
            if self.is_species or self.method != "supabase_jwt":
                return False
            if is_human_platform_grant:
                return True
            return permission in self.platform_permissions
        if is_human_platform_grant:
            return True
        if self.is_species or "owner" in self.permissions or "admin" in self.permissions:
            return True
        return permission in self.permissions

    def assert_workspace_access(self, target_workspace_id: str) -> None:
        """Raise PermissionError if this identity cannot touch the workspace."""
        if self.is_species or (
            self.method == "supabase_jwt" and self.platform_role == "super_admin"
        ):
            return
        if not self.workspace_id:
            raise PermissionError("No workspace bound to this credential")
        if self.workspace_id != target_workspace_id:
            raise PermissionError(
                f"Credential is scoped to workspace '{self.workspace_id}', "
                f"not '{target_workspace_id}'"
            )


class SpeciesAuth:
    """Single authentication implementation for the species."""

    def validate(
        self,
        *,
        bearer_token: str = "",
        api_key_header: str = "",
        supabase_client=None,
        key_store=None,
        requested_workspace_id: Optional[str] = None,
    ) -> AuthIdentity:
        """
        Validate credentials from headers only (never query string).

        Pass either Authorization Bearer value or X-API-Key value.
        """
        from config.settings import settings

        token = (bearer_token or api_key_header or "").strip()
        if not token:
            return AuthIdentity(valid=False, error="Authentication required")

        # ── 1. Master token (species operator) ──────────────────────────
        master = (getattr(settings, "master_api_token", None) or "").strip()
        if master and hmac.compare_digest(token, master):
            return AuthIdentity(
                valid=True,
                method="master",
                workspace_id=None,
                permissions=list(MASTER_PERMISSIONS),
                name="Master Token",
                is_species=True,
            )

        # ── 1b. Python service token (KCPG Gateway) ─────────────────────
        python_service = (getattr(settings, "python_service_token", None) or "").strip()
        if python_service and hmac.compare_digest(token, python_service):
            return AuthIdentity(
                valid=True,
                method="python_service",
                workspace_id=None,
                permissions=list(MASTER_PERMISSIONS),
                name="KCPG Gateway Service",
                is_species=True,
            )

        # ── 2. Supabase JWT ─────────────────────────────────────────────
        if token.startswith("eyJ") and supabase_client is not None:
            identity = self._validate_supabase_jwt(token, supabase_client, requested_workspace_id=requested_workspace_id)
            if identity.valid:
                if not identity.workspace_id:
                    return AuthIdentity(valid=False, error="Workspace token has no workspace scope")
                return identity

        # ── 3. Workspace API tokens ───────────────────────────────────────
        if token.startswith(("KDO_API_", "kny_", "kz_")):
            return self._validate_workspace_token(token, key_store=key_store)

        # Explicitly reject known-bad legacy methods if someone passes them
        app_secret = (getattr(settings, "app_secret", None) or "").strip()
        owner_psid = (getattr(settings, "owner_psid", None) or "").strip()
        if app_secret and hmac.compare_digest(token, app_secret):
            logger.warning("Rejected APP_SECRET used as API credential (Phase A)")
            return AuthIdentity(
                valid=False,
                error="APP_SECRET is not a valid API credential",
            )
        if owner_psid and hmac.compare_digest(token, owner_psid):
            logger.warning("Rejected owner PSID used as API credential (Phase A)")
            return AuthIdentity(
                valid=False,
                error="Owner PSID is not a valid API credential",
            )

        return AuthIdentity(valid=False, error="Invalid credentials")

    def _validate_supabase_jwt(self, token: str, client, requested_workspace_id: Optional[str] = None) -> AuthIdentity:
        try:
            user_resp = client.auth.get_user(token)
            if not user_resp or not user_resp.user:
                return AuthIdentity(valid=False, error="Invalid JWT")

            email = user_resp.user.email or ""
            user_id = getattr(user_resp.user, "id", None) or ""
            workspace_id, membership_role = self._resolve_workspace_for_email(client, email, user_id, requested_workspace_id=requested_workspace_id)

            normalized_role = normalize_role(membership_role)
            permissions = {"viewer": ["read"], "operator": ["read", "write"], "admin": ["read", "write", "admin"], "owner": ["read", "write", "admin", "owner"]}[normalized_role]

            # Platform authority is a distinct, server-configured human grant.
            # Never read role/scope from JWT user_metadata or workspace membership.
            from config.settings import settings
            platform_role, platform_permissions = resolve_platform_authority(
                user_id,
                super_admin_ids=getattr(settings, "platform_super_admin_user_ids", ""),
                operator_ids=getattr(settings, "platform_operator_user_ids", ""),
            )

            return AuthIdentity(
                valid=True,
                method="supabase_jwt",
                workspace_id=workspace_id,
                user_email=email,
                user_id=str(user_id) if user_id else None,
                permissions=permissions,
                role=normalized_role,
                name=email or "jwt-user",
                is_species=False,
                platform_role=platform_role,
                platform_permissions=platform_permissions,
            )
        except PermissionError as exc:
            logger.warning("JWT authenticated but not workspace-authorized: %s", exc)
            return AuthIdentity(valid=False, error=str(exc))
        except Exception as exc:
            logger.debug("JWT validation failed: %s", exc)
            return AuthIdentity(valid=False, error="JWT validation failed")

    def _resolve_workspace_for_email(
        self, client, email: str, user_id: str, requested_workspace_id: Optional[str] = None
    ) -> tuple[str, str]:
        """Resolve workspace and role from membership/ownership — never client header."""
        try:
            if user_id:
                query = (
                    client.table("workspace_members")
                    .select("workspace_id, role, accepted_at")
                    .eq("user_id", str(user_id))
                )
                if requested_workspace_id:
                    query = query.eq("workspace_id", requested_workspace_id)
                mem = query.execute()
                accepted = [row for row in (mem.data or []) if row.get("accepted_at") is not None]
                if accepted:
                    accepted.sort(key=lambda row: str(row.get("accepted_at") or ""), reverse=True)
                    return accepted[0]["workspace_id"], str(accepted[0].get("role") or "viewer")
        except Exception as exc:
            logger.debug("workspace_members lookup failed: %s", exc)

        try:
            if email:
                query = client.table("workspaces").select("id").eq("owner_email", email)
                if requested_workspace_id:
                    query = query.eq("id", requested_workspace_id)
                else:
                    query = query.limit(1)
                res = query.execute()
                if res.data:
                    return res.data[0]["id"], "owner"
        except Exception as exc:
            logger.debug("workspaces owner_email lookup failed: %s", exc)

        # Never silently place an authenticated user into a default tenant.
        # A JWT without an explicit membership/ownership binding is authenticated
        # but not authorized for any workspace.
        raise PermissionError("Authenticated user has no workspace membership")

    def _validate_workspace_token(
        self, token: str, key_store=None
    ) -> AuthIdentity:
        token_hash = hashlib.sha256(token.encode()).hexdigest()

        # Prefer APITokenManager (KDO_API_ tokens; legacy prefixes remain valid)
        try:
            from core.api_tokens import api_token_manager

            record = api_token_manager.validate_token(token)
            if record:
                purpose = record.get("credential_purpose") or "workspace_api"
                constraints = record.get("resource_constraints") or {}
                if purpose != "workspace_api" or not isinstance(constraints, dict):
                    return AuthIdentity(valid=False, error="Credential purpose is not enabled for this endpoint")
                workspace_id = record.get("workspace_id") or record.get("tenant_id")
                if not workspace_id:
                    return AuthIdentity(valid=False, error="Workspace token has no workspace scope")
                expires = record.get("expires_at")
                if expires:
                    try:
                        exp = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
                        if exp < datetime.now(timezone.utc):
                            return AuthIdentity(valid=False, error="API key expired")
                    except (TypeError, ValueError):
                        return AuthIdentity(valid=False, error="API key has invalid expiry")
                permissions = list(record.get("permissions") or ["read"])
                return AuthIdentity(
                    valid=True,
                    method="workspace_token",
                    workspace_id=workspace_id,
                    role=_workspace_token_role(permissions),
                    permissions=permissions,
                    name=record.get("name") or "Scoped Token",
                    is_species=False,
                    credential_purpose=purpose,
                    resource_constraints=constraints,
                )
        except Exception as exc:
            logger.debug("api_token_manager validate failed: %s", exc)

        # Initialize the fallback record even when the Supabase client is
        # unavailable or the lookup returns no row. Invalid tokens must fail
        # closed with an auth response, never an UnboundLocalError.
        found = None

        # Canonical DB fallback for workspace API tokens. This must run before the
        # legacy-key feature gate: the gate controls only api_keys, never api_tokens.
        try:
            from services.supabase import supabase_service

            if supabase_service and supabase_service.client:
                res = (
                    supabase_service.client.table("api_tokens")
                    .select("*")
                    .eq("token_hash", token_hash)
                    .eq("revoked", False)
                    .maybe_single()
                    .execute()
                )
                if res and res.data:
                    found = res.data
        except Exception as exc:
            logger.warning("Canonical api_tokens lookup failed: %s", exc)

        if found and not found.get("revoked", False):
            purpose = found.get("credential_purpose") or "workspace_api"
            constraints = found.get("resource_constraints") or {}
            if purpose != "workspace_api" or not isinstance(constraints, dict):
                return AuthIdentity(valid=False, error="Credential purpose is not enabled for this endpoint")
            expires = found.get("expires_at")
            if expires:
                try:
                    exp = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
                    if exp < datetime.now(timezone.utc):
                        return AuthIdentity(valid=False, error="API key expired")
                except (TypeError, ValueError):
                    return AuthIdentity(valid=False, error="API key has invalid expiry")

            workspace_id = found.get("workspace_id") or found.get("tenant_id")
            if not workspace_id:
                return AuthIdentity(valid=False, error="API key has no workspace scope")
            permissions = list(found.get("permissions") or ["read"])
            return AuthIdentity(
                valid=True,
                method="workspace_token",
                workspace_id=workspace_id,
                role=_workspace_token_role(permissions),
                permissions=permissions,
                name=found.get("name") or "Scoped Token",
                is_species=False,
                credential_purpose=purpose,
                resource_constraints=constraints,
            )

        # Legacy api_keys table — OFF unless explicitly enabled.
        # This gate must never prevent canonical api_tokens validation above.
        import os as _os_leg
        if not (_os_leg.getenv("ALLOW_LEGACY_API_KEYS") or "").strip().lower() in ("1", "true", "yes"):
            return AuthIdentity(valid=False, error="Legacy api_keys disabled — use api_tokens (KDO_API_)")

        found = None
        if key_store is not None:
            try:
                if hasattr(key_store, "find_by_hash_sync"):
                    found = key_store.find_by_hash_sync(token_hash)
                elif hasattr(key_store, "_store"):
                    for entry in getattr(key_store, "_store", {}).values():
                        if entry.get("hashed") == token_hash or entry.get("key_hash") == token_hash:
                            found = entry
                            break
            except Exception:
                pass

        if found and not found.get("revoked", False):
            expires = found.get("expires_at")
            if expires:
                try:
                    exp = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
                    if exp < datetime.now(timezone.utc):
                        return AuthIdentity(valid=False, error="API key expired")
                except (TypeError, ValueError):
                    return AuthIdentity(valid=False, error="API key has invalid expiry")
            workspace_id = found.get("workspace_id") or found.get("tenant_id")
            if not workspace_id:
                return AuthIdentity(valid=False, error="API key has no workspace scope")
            return AuthIdentity(
                valid=True,
                method="user_api_key",
                workspace_id=workspace_id,
                permissions=list(found.get("permissions") or ["read"]),
                name=found.get("label") or found.get("name") or "API Key",
                is_species=False,
            )

        return AuthIdentity(valid=False, error="Invalid API key")


def validate_species_workspace(target_workspace_id: str, client=None) -> bool:
    """Validate that a species-selected workspace exists and is active.

    Master/species credentials may operate across tenants, but a client-supplied
    workspace selector is still validated before it becomes request context.
    """
    ws = (target_workspace_id or "").strip()
    if not ws or len(ws) > 128 or any(c in ws for c in (" ", "\n", "\r")):
        return False
    if client is None:
        return False
    try:
        result = (client.table("workspaces")
                  .select("id,status")
                  .eq("id", ws)
                  .limit(1)
                  .execute())
        if not result or not result.data:
            return False
        status = str(result.data[0].get("status") or "active").lower()
        return status == "active"
    except Exception as exc:
        logger.warning("Species workspace validation failed for %s: %s", ws[:64], exc)
        return False


species_auth = SpeciesAuth()


def extract_token_from_request(request) -> tuple[str, str]:
    """
    Extract bearer and X-API-Key from request headers only.
    Never reads query parameters.
    """
    bearer = ""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        bearer = auth_header[7:].strip()

    api_key = request.headers.get("X-API-Key", "").strip()
    return bearer, api_key
