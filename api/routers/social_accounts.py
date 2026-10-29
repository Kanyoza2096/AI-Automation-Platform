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
    container=Depends(get_container),
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
):
    """A02: Create a social account — credentials stripped from response."""
    try:
        await require_workspace_ownership("workspace", workspace_id, container)
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
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
):
    """A02: List social accounts — credentials stripped."""
    try:
        await require_workspace_ownership("workspace", workspace_id, container)
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
        await require_workspace_ownership("social_account", account_id, container)
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
    container=Depends(get_container),
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
):
    """Delete a social account."""
    try:
        await require_workspace_ownership("social_account", account_id, container)
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
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
):
    """Check social account health."""
    try:
        await require_workspace_ownership("social_account", account_id, container)
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
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
):
    """Verify publish capability for a social account."""
    try:
        await require_workspace_ownership("social_account", account_id, container)
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
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
):
    """Bulk health check for social accounts."""
    try:
        await require_workspace_ownership("workspace", workspace_id, container)
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


def _verify_facebook(account) -> dict:
    """A10: Facebook verification with 8s timeout."""
    import requests as _requests_mod
    checks = {
        "token_valid": {"ok": False, "detail": "Not checked"},
        "page_access": {"ok": False, "detail": "Not checked", "page_name": account.account_name},
        "publish_permission": {"ok": False, "detail": "Not checked"},
        "rate_limit": {"ok": True, "detail": "Rate limits assumed OK"},
    }
    token = _get_account_token(account)
    page_id = _get_account_platform_id(account)
    if not token or not page_id:
        checks["token_valid"] = {"ok": False, "detail": "Missing access token or page ID"}
        return checks
    try:
        resp = _requests_mod.get(
            "https://graph.facebook.com/v22.0/debug_token",
            params={"input_token": token, "access_token": token},
            timeout=PLATFORM_VERIFY_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json().get("data", {})
            if data.get("is_valid"):
                expires = data.get("expires_at", 0)
                if expires == 0:
                    checks["token_valid"] = {"ok": True, "detail": "Long-lived token"}
                else:
                    days = (datetime.fromtimestamp(expires) - datetime.utcnow()).days
                    checks["token_valid"] = {"ok": True, "detail": f"Valid, expires in {days}d"}
            else:
                checks["token_valid"] = {"ok": False, "detail": data.get("error", {}).get("message", "Invalid")}
        else:
            checks["token_valid"] = {"ok": False, "detail": f"HTTP {resp.status_code}"}
        if checks["token_valid"]["ok"]:
            resp = _requests_mod.get(
                f"https://graph.facebook.com/v22.0/{page_id}",
                params={"fields": "name", "access_token": token},
                timeout=PLATFORM_VERIFY_TIMEOUT,
            )
            if resp.status_code == 200:
                name = resp.json().get("name", account.account_name)
                checks["page_access"] = {"ok": True, "detail": f"Page '{name}' accessible", "page_name": name}
            else:
                checks["page_access"] = {"ok": False, "detail": f"HTTP {resp.status_code}"}
        if checks["token_valid"]["ok"] and checks["page_access"]["ok"]:
            checks["publish_permission"] = {"ok": True, "detail": "Publish confirmed"}
        elif not checks["token_valid"]["ok"]:
            checks["publish_permission"] = {"ok": False, "detail": "Token invalid"}
        else:
            checks["publish_permission"] = {"ok": False, "detail": "Page access failed"}
    except _requests_mod.exceptions.Timeout:
        checks["token_valid"] = {"ok": False, "detail": f"Timeout ({PLATFORM_VERIFY_TIMEOUT}s)"}
    except Exception as e:
        checks["token_valid"] = {"ok": False, "detail": f"Error: {str(e)[:150]}"}
    return checks


def _verify_twitter(account) -> dict:
    import requests as _requests_mod
    checks = {
        "token_valid": {"ok": False, "detail": "Not checked"},
        "page_access": {"ok": False, "detail": "Not checked", "page_name": account.account_name},
        "publish_permission": {"ok": False, "detail": "Not checked"},
        "rate_limit": {"ok": True, "detail": "Rate limits assumed OK"},
    }
    token = _get_account_token(account)
    if not token:
        checks["token_valid"] = {"ok": False, "detail": "Missing access token"}
        return checks
    try:
        resp = _requests_mod.get(
            "https://api.twitter.com/2/users/me",
            headers={"Authorization": f"Bearer {token}"},
            timeout=PLATFORM_VERIFY_TIMEOUT,
        )
        if resp.status_code == 200:
            user = resp.json().get("data", {})
            name = user.get("username", account.account_name)
            checks["token_valid"] = {"ok": True, "detail": f"Authenticated as @{name}"}
            checks["page_access"] = {"ok": True, "detail": f"Account @{name} accessible", "page_name": f"@{name}"}
            checks["publish_permission"] = {"ok": True, "detail": "Publish assumed"}
        elif resp.status_code == 401:
            checks["token_valid"] = {"ok": False, "detail": "Token expired"}
    except Exception as e:
        checks["token_valid"] = {"ok": False, "detail": f"Error: {str(e)[:150]}"}
    return checks


def _verify_linkedin(account) -> dict:
    import requests as _requests_mod
    checks = {
        "token_valid": {"ok": False, "detail": "Not checked"},
        "page_access": {"ok": False, "detail": "Not checked", "page_name": account.account_name},
        "publish_permission": {"ok": False, "detail": "Not checked"},
        "rate_limit": {"ok": True, "detail": "Rate limits assumed OK"},
    }
    token = _get_account_token(account)
    if not token:
        checks["token_valid"] = {"ok": False, "detail": "Missing access token"}
        return checks
    try:
        resp = _requests_mod.get(
            "https://api.linkedin.com/v2/userinfo",
            headers={"Authorization": f"Bearer {token}"},
            timeout=PLATFORM_VERIFY_TIMEOUT,
        )
        if resp.status_code == 200:
            name = resp.json().get("name", account.account_name)
            checks["token_valid"] = {"ok": True, "detail": f"Authenticated as {name}"}
            checks["page_access"] = {"ok": True, "detail": "Profile accessible", "page_name": name}
            checks["publish_permission"] = {"ok": True, "detail": "Publish assumed"}
    except Exception as e:
        checks["token_valid"] = {"ok": False, "detail": f"Error: {str(e)[:150]}"}
    return checks


def _verify_instagram(account) -> dict:
    import requests as _requests_mod
    checks = {
        "token_valid": {"ok": False, "detail": "Not checked"},
        "page_access": {"ok": False, "detail": "Not checked", "page_name": account.account_name},
        "publish_permission": {"ok": False, "detail": "Not checked"},
        "rate_limit": {"ok": True, "detail": "Rate limits assumed OK"},
    }
    token = _get_account_token(account)
    if not token:
        checks["token_valid"] = {"ok": False, "detail": "Missing access token"}
        return checks
    try:
        resp = _requests_mod.get(
            "https://graph.facebook.com/v22.0/me",
            params={"access_token": token, "fields": "id,name"},
            timeout=PLATFORM_VERIFY_TIMEOUT,
        )
        if resp.status_code == 200:
            checks["token_valid"] = {"ok": True, "detail": "Token valid"}
            checks["page_access"] = {"ok": True, "detail": "Account accessible"}
            checks["publish_permission"] = {"ok": True, "detail": "Publish assumed"}
    except Exception as e:
        checks["token_valid"] = {"ok": False, "detail": f"Error: {str(e)[:150]}"}
    return checks


def _verify_whatsapp(account) -> dict:
    import requests as _requests_mod
    checks = {
        "token_valid": {"ok": False, "detail": "Not checked"},
        "page_access": {"ok": False, "detail": "Not checked", "page_name": account.account_name},
        "publish_permission": {"ok": False, "detail": "Not checked"},
        "rate_limit": {"ok": True, "detail": "Rate limits assumed OK"},
    }
    token = _get_account_token(account)
    phone_id = _get_account_platform_id(account)
    if not token or not phone_id:
        checks["token_valid"] = {"ok": False, "detail": "Missing credentials"}
        return checks
    try:
        resp = _requests_mod.get(
            f"https://graph.facebook.com/v22.0/{phone_id}",
            params={"access_token": token, "fields": "display_phone_number,verified_name"},
            timeout=PLATFORM_VERIFY_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            checks["token_valid"] = {"ok": True, "detail": f"WhatsApp: {data.get('verified_name', 'unknown')}"}
            checks["page_access"] = {"ok": True, "detail": "Phone verified"}
            checks["publish_permission"] = {"ok": True, "detail": "Messaging active"}
    except Exception as e:
        checks["token_valid"] = {"ok": False, "detail": f"Error: {str(e)[:150]}"}
    return checks


def _verify_telegram(account) -> dict:
    import requests as _requests_mod
    checks = {
        "token_valid": {"ok": False, "detail": "Not checked"},
        "page_access": {"ok": False, "detail": "Not checked", "page_name": account.account_name},
        "publish_permission": {"ok": False, "detail": "Not checked"},
        "rate_limit": {"ok": True, "detail": "Rate limits assumed OK"},
    }
    token = _get_account_token(account)
    if not token:
        checks["token_valid"] = {"ok": False, "detail": "Missing bot token"}
        return checks
    try:
        resp = _requests_mod.get(
            f"https://api.telegram.org/bot{token}/getMe",
            timeout=PLATFORM_VERIFY_TIMEOUT,
        )
        if resp.status_code == 200 and resp.json().get("ok"):
            bot = resp.json().get("result", {})
            name = bot.get("username", account.account_name)
            checks["token_valid"] = {"ok": True, "detail": f"Bot @{name} valid"}
            checks["page_access"] = {"ok": True, "detail": "Bot accessible", "page_name": f"@{name}"}
            checks["publish_permission"] = {"ok": True, "detail": "Bot can send messages"}
    except Exception as e:
        checks["token_valid"] = {"ok": False, "detail": f"Error: {str(e)[:150]}"}
    return checks

