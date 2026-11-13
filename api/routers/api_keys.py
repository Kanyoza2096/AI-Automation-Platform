from fastapi import APIRouter, Depends, Request, HTTPException
from typing import Optional
from datetime import datetime, timedelta, timezone
import logging

from api.dependencies import *
from api.v1_models import *
from api.v1_helpers import *
from api.v1_stores import *

from core.plugin_execution import resolve_plugin_workspace

from config.settings import settings
from core.security.deps import require_human_workspace_admin

logger = logging.getLogger(__name__)

router = APIRouter()

# ═══════════════════════════════════════════════════════════════════════════
# API KEYS — A02/A07
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/keys/generate")
async def generate_api_key(
    body: APIKeyGenerateRequest,
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
    _admin=Depends(require_workspace_admin),
    _rate=create_rate_limit_dependency("key_generation", max_per_minute=settings.api_rate_limit_key_generation_per_user),
):
    """Generate a durable, workspace-scoped API token.

    Tokens are managed by APITokenManager and are never stored in the legacy
    global ``api_keys`` store. The plaintext token is returned only once.
    """
    try:
        workspace_id = resolve_plugin_workspace()
        from core.api_tokens import api_token_manager
        expires_at = (
            (datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)).isoformat()
            if body.expires_in_days is not None else None
        )
        result = await _async_wrap(
            api_token_manager.create_token,
            workspace_id,
            body.label,
            body.scopes,
            expires_at,
        )
        await append_audit(
            audit_log, "api_key_created", f"key:{result['id']}", status_str="success",
        )
        return _ok({
            "key_id": result["id"],
            "token": result["token"],
            "label": result["name"],
            "permissions": result["permissions"],
            "scopes": result["permissions"],
            "expires_at": expires_at,
            "note": "Store this token securely. It will not be shown again.",
        }, 201)
    except RuntimeError as exc:
        return _err(str(exc), 503)
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/keys/generate")


@router.get("/keys")
async def list_api_keys(
    _auth: bool = Depends(require_auth),
    _admin=Depends(require_workspace_admin),
):
    """List active API tokens for the trusted workspace only."""
    try:
        workspace_id = resolve_plugin_workspace()
        from core.api_tokens import api_token_manager
        keys = await _async_wrap(api_token_manager.list_tokens, workspace_id)
        return _ok({"keys": keys, "count": len(keys), "workspace_id": workspace_id})
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/keys")


@router.delete("/keys/{key_id}")
async def revoke_api_key(
    key_id: str,
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
    _admin=Depends(require_human_workspace_admin),
):
    """Revoke a workspace-scoped API token."""
    try:
        workspace_id = resolve_plugin_workspace()
        from core.api_tokens import api_token_manager
        revoked = await _async_wrap(api_token_manager.revoke_token, key_id, workspace_id)
        if not revoked:
            return _err("API key not found", 404)
        await append_audit(
            audit_log, "api_key_revoked", f"key:{key_id}", status_str="success",
        )
        return _ok({"revoked": True, "key_id": key_id})
    except Exception as exc:
        return _safe_err(exc, f"DELETE /api/v1/keys/{key_id}")
