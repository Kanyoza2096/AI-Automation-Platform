"""
API Token management — per-workspace token generation, validation, and revocation.

Tokens are stored as SHA-256 hashes in Supabase (never in plaintext).
Token format: ``kny_<32-byte-hex>``
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("kanyoza.core.api_tokens")


class APITokenManager:
    """Manages workspace-scoped API tokens.

    Tokens are validated per-request by looking up the hashed value in
    the ``api_tokens`` table. The plaintext token is only shown once
    at creation time.
    """

    _PREFIX = "kny_"

    def __init__(self, supabase_client=None) -> None:
        self._db = supabase_client
        # In-memory fallback for single-tenant / dev mode
        self._tokens: Dict[str, Dict] = {}

    # ── Generation ────────────────────────────────────────────────────

    def create_token(
        self,
        workspace_id: str,
        name: str = "Default",
        permissions: Optional[List[str]] = None,
        expires_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a new API token for a workspace.

        Returns the plaintext token (shown **once**) and token metadata.
        """
        raw_token = f"{self._PREFIX}{secrets.token_hex(32)}"
        token_hash = self._hash(raw_token)
        token_id = f"tok-{uuid.uuid4().hex[:12]}"

        record = {
            "id": token_id,
            "workspace_id": workspace_id,
            "name": name,
            "token_hash": token_hash,
            "permissions": permissions or ["read"],
            "expires_at": expires_at,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_used_at": None,
            "revoked": False,
        }

        if self._db:
            try:
                self._db.table("api_tokens").insert(record).execute()
            except Exception as exc:
                logger.error("APITokenManager.create_token DB error: %s", exc)
        else:
            self._tokens[token_hash] = record

        logger.info("API token created: id=%s workspace=%s name='%s'", token_id, workspace_id, name)
        return {"token": raw_token, "id": token_id, "name": name, "permissions": permissions or ["read"]}

    # ── Validation ────────────────────────────────────────────────────

    def validate_token(self, raw_token: str) -> Optional[Dict[str, Any]]:
        """Validate a token. Returns the token record if valid, else None."""
        if not raw_token or not raw_token.startswith(self._PREFIX):
            return None

        token_hash = self._hash(raw_token)

        if self._db:
            try:
                res = (
                    self._db.table("api_tokens")
                    .select("*")
                    .eq("token_hash", token_hash)
                    .eq("revoked", False)
                    .maybe_single()
                    .execute()
                )
                record = res.data if res else None
            except Exception as exc:
                logger.error("APITokenManager.validate_token DB error: %s", exc)
                record = None
        else:
            record = self._tokens.get(token_hash)

        if not record:
            return None

        # Expiry check
        if record.get("expires_at"):
            try:
                exp = datetime.fromisoformat(record["expires_at"])
                if datetime.now(timezone.utc) > exp:
                    logger.warning("API token expired: %s", record["id"])
                    return None
            except Exception:
                pass

        # Update last_used_at (best-effort)
        self._touch(record["id"])
        return record

    # ── Revocation ────────────────────────────────────────────────────

    def revoke_token(self, token_id: str, workspace_id: str) -> bool:
        """Revoke a token by ID. Only the owning workspace can revoke it."""
        if self._db:
            try:
                self._db.table("api_tokens").update({"revoked": True}).eq("id", token_id).eq("workspace_id", workspace_id).execute()
                return True
            except Exception as exc:
                logger.error("APITokenManager.revoke_token error: %s", exc)
                return False
        else:
            for h, rec in self._tokens.items():
                if rec["id"] == token_id and rec["workspace_id"] == workspace_id:
                    rec["revoked"] = True
                    return True
            return False

    def list_tokens(self, workspace_id: str) -> List[Dict]:
        """List all active tokens for a workspace (never shows the raw token)."""
        if self._db:
            try:
                res = self._db.table("api_tokens").select("id,name,permissions,created_at,last_used_at,expires_at,revoked").eq("workspace_id", workspace_id).execute()
                return res.data or []
            except Exception as exc:
                logger.error("APITokenManager.list_tokens error: %s", exc)
                return []
        return [
            {k: v for k, v in rec.items() if k != "token_hash"}
            for rec in self._tokens.values()
            if rec["workspace_id"] == workspace_id
        ]

    # ── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def _touch(self, token_id: str) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        if self._db:
            try:
                self._db.table("api_tokens").update({"last_used_at": ts}).eq("id", token_id).execute()
            except Exception:
                pass
        else:
            for rec in self._tokens.values():
                if rec["id"] == token_id:
                    rec["last_used_at"] = ts


# Module-level singleton
api_token_manager = APITokenManager()
