from fastapi import APIRouter, Depends, Request, HTTPException
from typing import Optional
from api.dependencies import *
from api.v1_models import *
from api.v1_helpers import *
from api.v1_stores import *

router = APIRouter()

# ═══════════════════════════════════════════════════════════════════════════
# USERS CRUD — Workspace-scoped RBAC
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/workspaces/{workspace_id}/users")
async def list_workspace_users(
    workspace_id: str,
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
    _admin=Depends(require_workspace_admin),
):
    """List all users in a workspace. Requires admin or above."""
    try:
        await require_workspace_ownership("workspace", workspace_id, container)

        from repositories.workspace_member_repository import WorkspaceMemberRepository
        repo = await _async_wrap(container.resolve, WorkspaceMemberRepository)
        members = await _async_wrap(repo.find_by_workspace, workspace_id)

        return _ok({
            "users": [
                {
                    "id": m.id,
                    "user_id": m.user_id,
                    "role": m.role.value,
                    "invited_at": m.invited_at.isoformat() if m.invited_at else None,
                    "accepted_at": m.accepted_at.isoformat() if m.accepted_at else None,
                }
                for m in members
            ],
            "count": len(members),
        })
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"GET /api/v1/workspaces/{workspace_id}/users")


@router.post("/workspaces/{workspace_id}/users")
async def add_workspace_user(
    workspace_id: str,
    body: UserCreateRequest,
    container=Depends(get_container),
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
    _admin=Depends(require_workspace_admin),
):
    """Add a user to a workspace. Requires admin or above."""
    try:
        await require_workspace_ownership("workspace", workspace_id, container)

        from models.workspace import WorkspaceMember, MemberRole
        from repositories.workspace_member_repository import WorkspaceMemberRepository
        import uuid as _uuid_mod

        repo = await _async_wrap(container.resolve, WorkspaceMemberRepository)

        # Check for duplicate email
        existing = await _async_wrap(repo.find_by_user_and_workspace, body.email, workspace_id)
        if existing:
            return _err(f"User '{body.email}' is already a member of this workspace", 409)

        member = WorkspaceMember(
            id=f"member-{_uuid_mod.uuid4().hex[:12]}",
            workspace_id=workspace_id,
            user_id=body.email,
            role=MemberRole(body.role),
        )

        saved = await _async_wrap(repo.save, member)

        await append_audit(
            audit_log, "user_added", f"workspace:{workspace_id}/user:{body.email}",
            status_str="success",
            detail=f"Role: {body.role}",
        )

        return _ok({
            "user": {
                "id": saved.id,
                "user_id": saved.user_id,
                "role": saved.role.value,
                "invited_at": saved.invited_at.isoformat(),
            },
        }, 201)
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"POST /api/v1/workspaces/{workspace_id}/users")


@router.put("/workspaces/{workspace_id}/users/{user_id}")
async def update_workspace_user(
    workspace_id: str,
    user_id: str,
    body: UserUpdateRequest,
    container=Depends(get_container),
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
    _admin=Depends(require_workspace_admin),
):
    """Update a user's role. Requires admin or above. Cannot modify yourself."""
    try:
        await require_workspace_ownership("workspace", workspace_id, container)

        from repositories.workspace_member_repository import WorkspaceMemberRepository
        from models.workspace import MemberRole

        # Prevent self-modification
        current_user = auth_user_email_var.get()
        if current_user and current_user == user_id:
            return _err("You cannot modify your own role", 403)

        repo = await _async_wrap(container.resolve, WorkspaceMemberRepository)
        member = await _async_wrap(repo.find_by_user_and_workspace, user_id, workspace_id)

        if not member:
            return _err("User not found in this workspace", 404)

        old_role = member.role.value

        if body.role:
            # Prevent removing the last owner
            if old_role == "owner" and body.role != "owner":
                all_members = await _async_wrap(repo.find_by_workspace, workspace_id)
                owner_count = sum(1 for m in all_members if m.role == MemberRole.OWNER)
                if owner_count <= 1:
                    return _err("Cannot remove the last owner of a workspace", 403)

            member.role = MemberRole(body.role)

        member.updated_at = datetime.now(timezone.utc).isoformat()
        saved = await _async_wrap(repo.save, member)

        await append_audit(
            audit_log, "user_updated", f"workspace:{workspace_id}/user:{user_id}",
            status_str="success",
            detail=f"Role: {old_role} → {saved.role.value}",
        )

        return _ok({
            "user": {
                "id": saved.id,
                "user_id": saved.user_id,
                "role": saved.role.value,
            },
        })
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"PUT /api/v1/workspaces/{workspace_id}/users/{user_id}")


@router.delete("/workspaces/{workspace_id}/users/{user_id}")
async def remove_workspace_user(
    workspace_id: str,
    user_id: str,
    container=Depends(get_container),
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
    _admin=Depends(require_workspace_admin),
):
    """Remove a user from a workspace. Requires admin or above."""
    try:
        await require_workspace_ownership("workspace", workspace_id, container)

        from repositories.workspace_member_repository import WorkspaceMemberRepository
        from models.workspace import MemberRole

        # Prevent self-deletion
        current_user = auth_user_email_var.get()
        if current_user and current_user == user_id:
            return _err("You cannot remove yourself from a workspace", 403)

        repo = await _async_wrap(container.resolve, WorkspaceMemberRepository)
        member = await _async_wrap(repo.find_by_user_and_workspace, user_id, workspace_id)

        if not member:
            return _err("User not found in this workspace", 404)

        # Prevent deleting the last owner
        if member.role == MemberRole.OWNER:
            all_members = await _async_wrap(repo.find_by_workspace, workspace_id)
            owner_count = sum(1 for m in all_members if m.role == MemberRole.OWNER)
            if owner_count <= 1:
                return _err("Cannot remove the last owner of a workspace", 403)

        deleted = await _async_wrap(repo.delete, member.id)

        await append_audit(
            audit_log, "user_removed", f"workspace:{workspace_id}/user:{user_id}",
            status_str="success",
        )

        return _ok({"deleted": deleted})
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"DELETE /api/v1/workspaces/{workspace_id}/users/{user_id}")

