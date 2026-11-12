"""
API Token management — per-workspace token generation, validation, and revocation.

v2.0 FIXES:
  - create_token() now raises on DB persistence failure instead of
    silently returning a token that was never stored.
  - Added set_supabase_client() so lifecycle.py can wire the real client.
  - Added health_check() and get_stats() for MetaEngine compatibility.
  - validate_token() DB failure now falls back to in-memory safely
    with explicit logging of the degradation.

Tokens are stored as SHA-256 hashes in Supabase (never in plaintext).
Token format: ``KDO_API_<64-hex>`` (legacy ``kny_`` tokens remain valid)
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

    v2.0: DB persistence failures raise exceptions instead of
    silently returning unusable tokens.
    """

    _PREFIX = "KDO_API_"
    _LEGACY_PREFIXES = ("kny_", "kz_")

    def __init__(self, supabase_client=None, *, allow_in_memory: bool = False) -> None:
        self._db = supabase_client
        self._allow_in_memory = allow_in_memory
        self._tokens: Dict[str, Dict] = {}
        self._tokens_created = 0
        self._tokens_revoked = 0
        self._validations = 0

    # ═══════════════════════════════════════════════════════════════
    # v2.0 — WIRING (called by lifecycle.py)
    # ═══════════════════════════════════════════════════════════════

    def set_supabase_client(self, client) -> None:
        """Wire the Supabase client after initialization.

        Called by lifecycle.py during boot. Without this, all token
        operations fall back to in-memory storage which is lost on
        restart.
        """
        self._db = client
        logger.info(
            "APITokenManager: Supabase client wired — "
            "persistent token storage enabled"
        )

    # ═══════════════════════════════════════════════════════════════
    # GENERATION
    # ═══════════════════════════════════════════════════════════════

    def create_token(
        self,
        workspace_id: str,
        name: str = "Default",
        permissions: Optional[List[str]] = None,
        expires_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a new API token for a workspace.

        Returns the plaintext token (shown **once**) and token metadata.

        v2.0: Raises RuntimeError if DB persistence fails, rather than
        returning a token that was never stored.
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
                self._tokens_created += 1
            except Exception as exc:
                logger.error(
                    "APITokenManager.create_token: DB persistence FAILED "
                    "for token %s — token NOT created. Error: %s",
                    token_id, exc,
                )
                raise RuntimeError(
                    f"Failed to persist API token to database: {exc}"
                ) from exc
        else:
            # In-memory tokens are never a valid production persistence mode.
            # A restart would invalidate credentials and, more importantly,
            # authentication state would diverge across workers.
            try:
                from config.settings import settings
                if getattr(settings, "is_production", False) and not self._allow_in_memory:
                    raise RuntimeError("Durable API token database is required in production")
            except ImportError:
                pass
            self._tokens[token_hash] = record
            self._tokens_created += 1
            logger.warning(
                "APITokenManager: token %s stored IN-MEMORY only — "
                "will be lost on restart. Wire Supabase client.",
                token_id,
            )

        logger.info(
            "API token created: id=%s workspace=%s name='%s'",
            token_id, workspace_id, name,
        )
        return {
            "token": raw_token,
            "id": token_id,
            "name": name,
            "permissions": permissions or ["read"],
        }

    # ═══════════════════════════════════════════════════════════════
    # VALIDATION
    # ═══════════════════════════════════════════════════════════════

    def validate_token(
        self, raw_token: str
    ) -> Optional[Dict[str, Any]]:
        """Validate a token. Returns the token record if valid, else None.

        v2.0: DB failure during validation now logs a clear warning
        and falls back to in-memory rather than silently returning None.
        """
        if not raw_token or not raw_token.startswith((self._PREFIX, *self._LEGACY_PREFIXES)):
            return None

        token_hash = self._hash(raw_token)
        record = None
        db_error = False

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
                logger.error(
                    "APITokenManager.validate_token: DB query failed — "
                    "falling back to in-memory. Error: %s", exc,
                )
                db_error = True

        # Fallback to in-memory if DB unavailable
        if record is None and (not self._db or db_error):
            record = self._tokens.get(token_hash)

        if not record:
            return None

        # Expiry check
        if record.get("expires_at"):
            try:
                exp = datetime.fromisoformat(record["expires_at"])
                if datetime.now(timezone.utc) > exp:
                    logger.warning(
                        "API token expired: %s", record.get("id", "unknown")
                    )
                    return None
            except Exception as exc:
                # A persisted expiry that cannot be parsed is an invalid
                # credential record. Never fail open on malformed metadata.
                logger.error(
                    "API token has malformed expires_at: %s",
                    record.get("id", "unknown"),
                )
                return None

        # Update last_used_at (best-effort, non-blocking)
        self._touch(record.get("id", ""))
        self._validations += 1
        return record

    # ═══════════════════════════════════════════════════════════════
    # REVOCATION
    # ═══════════════════════════════════════════════════════════════

    def revoke_token(
        self, token_id: str, workspace_id: str
    ) -> bool:
        """Revoke a token by ID. Only the owning workspace can revoke it."""
        if self._db:
            try:
                self._db.table("api_tokens").update(
                    {"revoked": True}
                ).eq("id", token_id).eq(
                    "workspace_id", workspace_id
                ).execute()
                self._tokens_revoked += 1
                return True
            except Exception as exc:
                logger.error(
                    "APITokenManager.revoke_token error: %s", exc
                )
                return False
        else:
            for h, rec in self._tokens.items():
                if (
                    rec["id"] == token_id
                    and rec["workspace_id"] == workspace_id
                ):
                    rec["revoked"] = True
                    self._tokens_revoked += 1
                    return True
            return False

    def list_tokens(
        self, workspace_id: str
    ) -> List[Dict]:
        """List all active tokens for a workspace (never shows raw token)."""
        if self._db:
            try:
                res = (
                    self._db.table("api_tokens")
                    .select(
                        "id,name,permissions,created_at,"
                        "last_used_at,expires_at,revoked"
                    )
                    .eq("workspace_id", workspace_id)
                    .execute()
                )
                return res.data or []
            except Exception as exc:
                logger.error(
                    "APITokenManager.list_tokens error: %s", exc
                )
                return []
        return [
            {
                k: v
                for k, v in rec.items()
                if k != "token_hash"
            }
            for rec in self._tokens.values()
            if rec["workspace_id"] == workspace_id
        ]

    # ═══════════════════════════════════════════════════════════════
    # HELPERS
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def _touch(self, token_id: str) -> None:
        """Update last_used_at — best-effort, never blocks."""
        if not token_id:
            return
        ts = datetime.now(timezone.utc).isoformat()
        if self._db:
            try:
                self._db.table("api_tokens").update(
                    {"last_used_at": ts}
                ).eq("id", token_id).execute()
            except Exception:
                pass
        else:
            for rec in self._tokens.values():
                if rec.get("id") == token_id:
                    rec["last_used_at"] = ts

    # ═══════════════════════════════════════════════════════════════
    # v2.0 — HEALTH & STATISTICS
    # ═══════════════════════════════════════════════════════════════

    def get_stats(self) -> dict:
        return {
            "tokens_created": self._tokens_created,
            "tokens_revoked": self._tokens_revoked,
            "validations": self._validations,
            "persistent_storage": self._db is not None,
            "in_memory_tokens": len(self._tokens),
        }

    def health_check(self) -> dict:
        stats = self.get_stats()
        status = "healthy"
        if not stats["persistent_storage"]:
            status = "degraded"
        return {"status": status, "stats": stats}


# Module-level singleton — Supabase client wired by lifecycle.py
api_token_manager = APITokenManager()
