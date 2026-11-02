from fastapi import APIRouter, Depends, Request, HTTPException
from typing import Optional
from api.dependencies import *
from api.v1_models import *
from api.v1_helpers import *
from api.v1_stores import *

router = APIRouter()

# ═══════════════════════════════════════════════════════════════════════════
# SOCIAL ACCOUNTS — A02 Credential stripping
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/workspaces/{workspace_id}/social-accounts")
async def create_social_account(
    workspace_id: str,
    body: dict,
    request: Request,
    container=Depends(get_container),
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
):
    """A02: Create a social account — credentials stripped from response."""
    try:
        await require_workspace_ownership("workspace", workspace_id, request, container)
        from models.social_account import SocialAccount
        from repositories.social_account_repository import SocialAccountRepository

        acc_id = body.get("id") or f"acc-{_uuid_mod.uuid4().hex[:12]}"
        body["id"] = acc_id
        body["workspace_id"] = workspace_id
        if not body.get("platform"):
            return _err("platform is required", 400)

        PLATFORM_FIELDS = {
            "facebook": ["page_id", "page_access_token", "verify_token"],
            "twitter": ["client_id", "client_secret"],
            "linkedin": ["person_urn"],
            "instagram": ["page_id"],
            "whatsapp": ["phone_number_id"],
            "telegram": ["bot_token"],
        }
        platform = body.get("platform", "")
        if platform in PLATFORM_FIELDS:
            pc = body.get("platform_config", {}) or {}
            for field in PLATFORM_FIELDS[platform]:
                if field in body:
                    pc[field] = body.pop(field)
            body["platform_config"] = pc

        account = SocialAccount(**body)
        repo = await _async_wrap(container.resolve, SocialAccountRepository)
        saved = await _async_wrap(repo.save, account)

        response_data = _strip_credentials(saved.model_dump(mode="json"))

        await append_audit(
            audit_log, "social_account_created", f"social_account:{acc_id}", status_str="success",
        )
        return _ok({"social_account": response_data}, 201)
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"POST /api/v1/workspaces/{workspace_id}/social-accounts")


@router.get("/workspaces/{workspace_id}/social-accounts")
async def list_social_accounts(
    workspace_id: str,
    request: Request,
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
):
    """A02: List social accounts — credentials stripped."""
    try:
        await require_workspace_ownership("workspace", workspace_id, request, container)
        from repositories.social_account_repository import SocialAccountRepository
        repo = await _async_wrap(container.resolve, SocialAccountRepository)
        accounts = await _async_wrap(repo.find_by_workspace, workspace_id)

        safe = [_strip_credentials(a.model_dump(mode="json")) for a in accounts]
        return _ok({"social_accounts": safe})
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"GET /api/v1/workspaces/{workspace_id}/social-accounts")


@router.put("/social-accounts/{account_id}")
async def update_social_account(
    account_id: str,
    body: dict,
    request: Request,
    container=Depends(get_container),
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
):
    """
    Update a social account.
    Spec §4.2: if access_token is null/empty/omitted in the request,
    keep the existing credential — do not overwrite it.
    """
    try:
        await require_workspace_ownership("social_account", account_id, request, container)
        from models.social_account import SocialAccount
        from repositories.social_account_repository import SocialAccountRepository

        repo = await _async_wrap(container.resolve, SocialAccountRepository)
        account = await _async_wrap(repo.find_by_id, account_id)
        if not account:
            return _err("Social Account not found", 404)

        update_data = {
            k: v for k, v in body.items()
            if k not in ("id", "workspace_id", "created_at")
        }
        update_data["updated_at"] = datetime.now(timezone.utc).isoformat()

        acc_dict = account.model_dump(mode="json")

        # Spec §4.2: preserve existing credential when caller sends null/empty token
        for secret_field in ("access_token", "encrypted_credential"):
            if secret_field in update_data and not update_data[secret_field]:
                # Caller omitted or cleared the field — keep the stored value
                update_data.pop(secret_field)

        # Same rule for nested platform_config secret keys
        if "platform_config" in update_data and isinstance(update_data["platform_config"], dict):
            existing_pc = acc_dict.get("platform_config") or {}
            incoming_pc = update_data["platform_config"]
            for sk in _SECRET_KEYS:
                if sk in incoming_pc and not incoming_pc[sk]:
                    incoming_pc.pop(sk)
                    # Restore existing value if present
                    if sk in existing_pc:
                        incoming_pc[sk] = existing_pc[sk]
            update_data["platform_config"] = incoming_pc

        acc_dict.update(update_data)
        saved = await _async_wrap(repo.save, SocialAccount(**acc_dict))

        await append_audit(
            audit_log, "social_account_updated", f"social_account:{account_id}",
            status_str="success",
        )
        return _ok({"social_account": _strip_credentials(saved.model_dump(mode="json"))})
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"PUT /api/v1/social-accounts/{account_id}")


@router.delete("/social-accounts/{account_id}")
async def delete_social_account(
    account_id: str,
    request: Request,
    container=Depends(get_container),
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
):
    """Delete a social account."""
    try:
        await require_workspace_ownership("social_account", account_id, request, container)
        from repositories.social_account_repository import SocialAccountRepository
        repo = await _async_wrap(container.resolve, SocialAccountRepository)
        deleted = await _async_wrap(repo.delete, account_id)
        await append_audit(
            audit_log, "social_account_deleted", f"social_account:{account_id}",
            status_str="success",
        )
        return _ok({"deleted": deleted})
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"DELETE /api/v1/social-accounts/{account_id}")


@router.post("/social-accounts/{account_id}/health-check")
async def health_check_social_account(
    account_id: str,
    request: Request,
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
):
    """Check social account health."""
    try:
        await require_workspace_ownership("social_account", account_id, request, container)
        from repositories.social_account_repository import SocialAccountRepository
        from services.connectors.connector_factory import create_connector

        repo = await _async_wrap(container.resolve, SocialAccountRepository)
        account = await _async_wrap(repo.find_by_id, account_id)
        if not account:
            return _err("Social Account not found", 404)

        connector = await _async_wrap(create_connector, account)
        h = await _async_wrap(connector.health)
        await _async_wrap(
            repo.update_health_status,
            account.id,
            "healthy" if h.ok else "unhealthy",
        )
        return _ok({"healthy": h.ok, "latency_ms": h.latency_ms, "error": h.error})
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"POST /api/v1/social-accounts/{account_id}/health-check")


@router.post("/social-accounts/{account_id}/verify-publish")
async def verify_publish(
    account_id: str,
    request: Request,
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
):
    """Verify publish capability for a social account."""
    try:
        await require_workspace_ownership("social_account", account_id, request, container)
        from repositories.social_account_repository import SocialAccountRepository
        from models.social_account import SocialAccount

        repo = await _async_wrap(container.resolve, SocialAccountRepository)
        account = await _async_wrap(repo.find_by_id, account_id)
        if not account:
            return _err("Social Account not found", 404)

        raw = getattr(account.platform, "value", None) or str(account.platform)
        platform = raw.lower().replace("platform.", "")

        verifier_map = {
            "facebook": _verify_facebook,
            "twitter": _verify_twitter,
            "linkedin": _verify_linkedin,
            "instagram": _verify_instagram,
            "whatsapp": _verify_whatsapp,
            "telegram": _verify_telegram,
        }
        verifier = verifier_map.get(platform)
        if not verifier:
            return _err(f"Verification not supported for: {platform}", 400)

        checks = await _async_wrap(verifier, account)
        ready = all(c.get("ok", False) for c in checks.values())

        acc_dict = account.model_dump(mode="json")
        acc_dict["health_status"] = "healthy" if ready else "unhealthy"
        acc_dict["last_health_check"] = datetime.now(timezone.utc).isoformat()
        await _async_wrap(repo.save, SocialAccount(**acc_dict))

        failed = [
            k.replace("_", " ").title()
            for k, c in checks.items()
            if not c.get("ok")
        ]
        if ready:
            summary = "All checks passed. Ready to publish."
        elif len(failed) == 1:
            summary = f"{failed[0]} check failed"
        else:
            summary = f"{len(failed)} checks failed"

        return _ok({
            "verified": ready,
            "page_name": checks.get("page_access", {}).get(
                "page_name", account.account_name
            ),
            "summary": summary,
            "ready_to_publish": ready,
            "checks": checks,
        })
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"POST /api/v1/social-accounts/{account_id}/verify-publish")


@router.post("/workspaces/{workspace_id}/social-accounts/bulk-health-check")
async def bulk_health_check(
    workspace_id: str,
    request: Request,
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
):
    """Bulk health check for social accounts."""
    try:
        await require_workspace_ownership("workspace", workspace_id, request, container)
        from repositories.social_account_repository import SocialAccountRepository
        from services.connectors.connector_factory import create_connector

        repo = await _async_wrap(container.resolve, SocialAccountRepository)
        accounts = await _async_wrap(repo.find_by_workspace, workspace_id)
        if len(accounts) > 20:
            return _err("Bulk health check limited to 20 accounts", 400)

        results = []
        for account in accounts:
            try:
                connector = await _async_wrap(create_connector, account)
                h = await _async_wrap(connector.health)
                await _async_wrap(
                    repo.update_health_status,
                    account.id,
                    "healthy" if h.ok else "unhealthy",
                )
                results.append({
                    "id": account.id,
                    "platform": str(account.platform),
                    "account_name": account.account_name,
                    "healthy": h.ok,
                })
            except Exception as e:
                results.append({
                    "id": account.id,
                    "healthy": False,
                    "error": str(e)[:200],
                })

        healthy_count = sum(1 for r in results if r.get("healthy"))
        return _ok({
            "results": results,
            "total": len(results),
            "healthy": healthy_count,
            "unhealthy": len(results) - healthy_count,
        })
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(
            exc,
            f"POST /api/v1/workspaces/{workspace_id}/social-accounts/bulk-health-check",
        )


# ── Platform Verifiers (A10: SSRF Protection via timeout) ──────────────────
# Unchanged — verifier functions do not call require_workspace_ownership.
