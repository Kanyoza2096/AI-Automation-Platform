from fastapi import APIRouter, Depends, Request, HTTPException
from typing import Optional
from api.dependencies import *
from api.v1_models import *
from api.v1_helpers import *
from api.v1_stores import *

router = APIRouter()

# ═══════════════════════════════════════════════════════════════════════════
# USERS — Spec §2: User management with workspace membership + RBAC
#
# INCIDENT-2026-10-16-users:
#   GET /users accepted an unused `workspace_id` query parameter that was
#   silently ignored. The handler correctly used auth_workspace_id_var, but
#   the dead parameter invited a future "fix" that would have turned it into
#   a client-controlled scope. Removed.
#
#   POST /users and PUT /users/{id} had a correctness-correct but
#   fragile operator-precedence expression for the "may assign owner role"
#   check. Rewritten for clarity with the same semantics.
#
#   All four handlers are otherwise correctly scoped:
#     GET    /users              — auth workspace filter
#     POST   /users              — admin gate + duplicate email guard
#     PUT    /users/{id}         — ownership check via existing.workspace_id
#     DELETE /users/{id}         — ownership check + last-admin protection
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/users")
async def list_users(
    _auth: bool = Depends(require_auth),
):
    """
    Spec §2: List users in the caller's workspace.
    Requires authenticated identity; scoped to the workspace bound to
    the caller's credentials.
    """
    try:
        ws_id = auth_workspace_id_var.get()
        if not ws_id:
            return _err("No workspace bound to credentials", 403)
        users: list[dict] = []
        try:
            from services.supabase import supabase_service
            if supabase_service and supabase_service.client:
                result = await _async_wrap(
                    lambda: supabase_service.client.table("workspace_members")
                    .select("id, email, name, role, status, workspace_id, created_at, updated_at")
                    .eq("workspace_id", ws_id)
                    .execute()
                )
                users = result.data or []
        except Exception as qe:
            logger.warning("User list query failed: %s", qe)

        return _ok({"users": users, "count": len(users), "workspace_id": ws_id})
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/users")


@router.post("/users")
async def create_user(
    body: UserCreateRequest,
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
    _admin=Depends(require_workspace_admin),
):
    """
    Spec §2: Add a user to a workspace.
    Enforces: authenticated caller, workspace membership, duplicate email protection.
    Admin-level operation — only admins/owners may create users.
    """
    try:
        ws_id = resolve_plugin_workspace(body.workspace_id)
        caller = get_request_context()
        is_species = bool(caller and caller.species_privileges)
        is_owner = bool(caller and caller.role == "owner")
        if body.role == "owner" and not (is_species or is_owner):
            return _err("Only a workspace owner or platform operator may assign the owner role", 403)

        try:
            from services.supabase import supabase_service
            if supabase_service and supabase_service.client:
                # Duplicate email guard
                existing = await _async_wrap(
                    lambda: supabase_service.client.table("workspace_members")
                    .select("id")
                    .eq("workspace_id", ws_id)
                    .eq("email", body.email)
                    .execute()
                )
                if existing.data:
                    return _err(f"User '{body.email}' already exists in this workspace", 409)

                user_id = _uuid_mod.uuid4().hex[:16]
                record = {
                    "id": user_id,
                    "email": body.email,
                    "name": body.name,
                    "role": body.role,
                    "workspace_id": ws_id,
                    "status": "active",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
                await _async_wrap(
                    lambda: supabase_service.client.table("workspace_members")
                    .insert(record)
                    .execute()
                )
                await append_audit(
                    audit_log, "user_created", f"user:{user_id}",
                    user=auth_user_email_var.get() or "system", status_str="success",
                )
                return _ok({"user": record}, 201)
        except HTTPException:
            raise
        except Exception as qe:
            logger.error("User create failed: %s", qe)
            return _safe_err(qe, "POST /api/v1/users")

        return _err("User creation requires Supabase", 503)
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/users")


@router.put("/users/{user_id}")
async def update_user(
    user_id: str,
    body: UserUpdateRequest,
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
    _admin=Depends(require_workspace_admin),
):
    """
    Spec §2: Update a workspace user.
    Never allows a user to manipulate another workspace by changing the workspace_id.
    """
    try:
        ws_id = auth_workspace_id_var.get()
        if not ws_id:
            return _err("No workspace bound to credentials", 403)

        try:
            from services.supabase import supabase_service
            if supabase_service and supabase_service.client:
                # Resolve existing user — enforce workspace isolation
                existing_res = await _async_wrap(
                    lambda: supabase_service.client.table("workspace_members")
                    .select("*")
                    .eq("id", user_id)
                    .maybe_single()
                    .execute()
                )
                if not existing_res or not existing_res.data:
                    return _err("User not found", 404)
                existing = existing_res.data

                # Workspace isolation — cross-workspace access denied
                if existing.get("workspace_id") != ws_id:
                    raise HTTPException(status_code=404, detail="User not found")

                updates = body.model_dump(exclude_none=True)
                caller = get_request_context()
                is_species = bool(caller and caller.species_privileges)
                is_owner = bool(caller and caller.role == "owner")
                if updates.get("role") == "owner" and not (is_species or is_owner):
                    raise HTTPException(status_code=403, detail="Only a workspace owner or platform operator may assign the owner role")
                updates["updated_at"] = datetime.now(timezone.utc).isoformat()

                # Never allow workspace_id to change
                updates.pop("workspace_id", None)

                await _async_wrap(
                    lambda: supabase_service.client.table("workspace_members")
                    .update(updates)
                    .eq("id", user_id)
                    .execute()
                )
                await append_audit(
                    audit_log, "user_updated", f"user:{user_id}",
                    user=auth_user_email_var.get() or "system", status_str="success",
                )
                return _ok({"user": {**existing, **updates}})
        except HTTPException:
            raise
        except Exception as qe:
            return _safe_err(qe, f"PUT /api/v1/users/{user_id}")

        return _err("User updates require Supabase", 503)
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"PUT /api/v1/users/{user_id}")


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: str,
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
    _admin=Depends(require_workspace_admin),
):
    """
    Spec §2: Delete a user from a workspace.
    Last-admin protection: refuses if this is the last admin/owner in the workspace.
    Safe deletion — validates membership before removing.
    """
    try:
        ws_id = auth_workspace_id_var.get()
        if not ws_id:
            return _err("No workspace bound to credentials", 403)

        try:
            from services.supabase import supabase_service
            if supabase_service and supabase_service.client:
                # Resolve existing user
                existing_res = await _async_wrap(
                    lambda: supabase_service.client.table("workspace_members")
                    .select("*")
                    .eq("id", user_id)
                    .maybe_single()
                    .execute()
                )
                if not existing_res or not existing_res.data:
                    return _err("User not found", 404)
                existing = existing_res.data

                # Workspace isolation
                if existing.get("workspace_id") != ws_id:
                    raise HTTPException(status_code=404, detail="User not found")

                # Last-admin protection
                if existing.get("role") in ("admin", "owner"):
                    admins_res = await _async_wrap(
                        lambda: supabase_service.client.table("workspace_members")
                        .select("id")
                        .eq("workspace_id", existing["workspace_id"])
                        .in_("role", ["admin", "owner"])
                        .execute()
                    )
                    if len(admins_res.data or []) <= 1:
                        return _err(
                            "Cannot remove the last admin/owner from a workspace. "
                            "Promote another member first.",
                            409,
                        )

                await _async_wrap(
                    lambda: supabase_service.client.table("workspace_members")
                    .delete()
                    .eq("id", user_id)
                    .execute()
                )
                await append_audit(
                    audit_log, "user_deleted", f"user:{user_id}",
                    user=auth_user_email_var.get() or "system", status_str="success",
                )
                return _ok({"deleted": True, "user_id": user_id})
        except HTTPException:
            raise
        except Exception as qe:
            return _safe_err(qe, f"DELETE /api/v1/users/{user_id}")

        return _err("User deletion requires Supabase", 503)
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"DELETE /api/v1/users/{user_id}")
