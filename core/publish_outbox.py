"""
Sprint 3 — Durable publish outbox.

Intent is stored first; a drainer executes Graph/plugin publish.
In-memory store always works; Supabase table publish_outbox used when client set.

Receipt contract (v2):
  - `published` requires a concrete `platform_post_id`. A result claiming
    success without a post ID is downgraded to `uncertain`, never `published`.
  - Every claimed item records `page_id` and `account_id` (never the token)
    so reconciliation and audit can address the correct provider object.
  - Timeouts and transport errors remain `uncertain`, never `failed`, because
    the provider may have accepted the request before the response was lost.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
import base64
import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger("kanyoza.core.publish_outbox")


@dataclass
class OutboxItem:
    id: str
    workspace_id: str
    decision_id: str
    correlation_id: str
    idempotency_key: str = ""
    automation_job_id: Optional[str] = None
    brand_id: Optional[str] = None
    platform: str = "facebook"
    caption: Optional[str] = None
    topic: Optional[str] = None
    risk_level: str = "low"
    payload: Dict[str, Any] = field(default_factory=dict)
    status: str = "pending"  # pending|processing|published|failed|dead|uncertain
    attempts: int = 0
    max_attempts: int = 5
    last_error: Optional[str] = None
    platform_post_id: Optional[str] = None
    available_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    published_at: Optional[datetime] = None
    locked_until: Optional[datetime] = None
    locked_by: Optional[str] = None
    reconciliation_attempts: int = 0
    last_reconciled_at: Optional[datetime] = None
    reconciliation_error: Optional[str] = None
    consecutive_not_found: int = 0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        for k in ("available_at", "created_at", "updated_at", "published_at", "last_reconciled_at"):
            if d.get(k) is not None and hasattr(d[k], "isoformat"):
                d[k] = d[k].isoformat()
        return d


class PublishOutbox:
    def __init__(self, supabase_client=None) -> None:
        self._db = supabase_client
        self._lock = threading.RLock()
        self._mem: Dict[str, OutboxItem] = {}
        self._drainer_task: Optional[asyncio.Task] = None
        self._running = False
        self._container = None

    def set_supabase(self, client) -> None:
        self._db = client

    @staticmethod
    def _is_production() -> bool:
        try:
            from config.settings import settings
            return bool(getattr(settings, "is_production", False))
        except Exception:
            import os
            return os.getenv("ENVIRONMENT", "development").lower() == "production"

    def set_container(self, container) -> None:
        self._container = container

    # ── Receipt helpers ───────────────────────────────────────────────

    @staticmethod
    def _extract_receipt(result: Any) -> Dict[str, Any]:
        """Normalize a pipeline result into the fields we persist.

        The pipeline (canonical_pipeline) is expected to surface:
            status: "ok" | "error" | "uncertain"
            platform_post_id: str | None
            page_id: str | None
            account_id: str | None
            mode: str | None
            error: Any

        Legacy callers may only return `ok`/`platform_post_id`. We accept both.
        """
        if not isinstance(result, dict):
            return {
                "status": "error",
                "platform_post_id": None,
                "page_id": None,
                "account_id": None,
                "mode": None,
                "error": f"Unrecognized result type: {type(result).__name__}",
            }

        status = result.get("status")
        if status is None:
            # Legacy: `ok` boolean only
            status = "ok" if result.get("ok") else "error"

        # A "succeeded" status without a post ID must never reach the outbox
        # as success. Downgrade honestly.
        post_id = result.get("platform_post_id") or result.get("provider_object_id")
        if status == "ok" and not post_id:
            status = "uncertain"
            if not result.get("error"):
                result = {**result, "error": "Publish reported success without a post ID"}

        return {
            "status": status,
            "platform_post_id": post_id,
            "page_id": result.get("page_id"),
            "account_id": result.get("account_id"),
            "mode": result.get("mode"),
            "error": result.get("error"),
            "reconciliation_required": bool(result.get("reconciliation_required")) or status == "uncertain",
        }

    # ── Enqueue ───────────────────────────────────────────────────────

    def enqueue(
        self,
        *,
        workspace_id: str,
        decision_id: str,
        automation_job_id: Optional[str] = None,
        correlation_id: str,
        brand_id: Optional[str] = None,
        platform: str = "facebook",
        caption: Optional[str] = None,
        topic: Optional[str] = None,
        risk_level: str = "low",
        payload: Optional[Dict[str, Any]] = None,
        delay_seconds: float = 0,
        idempotency_key: Optional[str] = None,
    ) -> OutboxItem:
        workspace_id = (workspace_id or "").strip()
        if not workspace_id:
            raise ValueError("workspace_id is required")
        platform = (platform or "facebook").strip().lower()
        # One logical decision must map to one durable external side-effect intent.
        # This key remains stable across process restarts and queue retries.
        idem_material = "|".join([workspace_id, decision_id or "", platform, brand_id or ""])
        idempotency_key = idempotency_key or ("pub_" + hashlib.sha256(idem_material.encode("utf-8")).hexdigest())
        with self._lock:
            existing = next((x for x in self._mem.values() if x.workspace_id == workspace_id and x.idempotency_key == idempotency_key), None)
        if existing is not None:
            return existing
        # The primary key must remain globally unique even when the same caller
        # supplied idempotency key is legitimately reused in another workspace.
        stable_id = hashlib.sha256(
            f"{workspace_id}|{idempotency_key}".encode("utf-8")
        ).hexdigest()[:24]
        item = OutboxItem(
            id="obx_" + stable_id,
            workspace_id=workspace_id,
            idempotency_key=idempotency_key,
            decision_id=decision_id,
            automation_job_id=automation_job_id,
            correlation_id=correlation_id or uuid.uuid4().hex,
            brand_id=brand_id,
            platform=platform,
            caption=caption,
            topic=topic,
            risk_level=risk_level,
            payload=payload or {},
            available_at=datetime.now(timezone.utc) + timedelta(seconds=delay_seconds),
        )
        # Production must have durable persistence. Falling back to process memory
        # would make a successful enqueue disappear on restart.
        if self._is_production() and self._db is None:
            raise RuntimeError("Durable publish outbox unavailable: persistence is not configured")
        self._save(item)
        logger.info(
            "Outbox enqueued id=%s ws=%s decision=%s",
            item.id, item.workspace_id, item.decision_id,
        )
        return item

    def _save(self, item: OutboxItem) -> None:
        item.updated_at = datetime.now(timezone.utc)
        with self._lock:
            self._mem[item.id] = item
        if not self._db:
            return
        try:
            row = {
                "id": item.id,
                "workspace_id": item.workspace_id,
                "idempotency_key": item.idempotency_key,
                "brand_id": item.brand_id,
                "platform": item.platform,
                "decision_id": item.decision_id,
                "automation_job_id": item.automation_job_id,
                "correlation_id": item.correlation_id,
                "caption": item.caption,
                "topic": item.topic,
                "risk_level": item.risk_level,
                "payload": item.payload,
                "status": item.status,
                "attempts": item.attempts,
                "max_attempts": item.max_attempts,
                "last_error": item.last_error,
                "platform_post_id": item.platform_post_id,
                "available_at": item.available_at.isoformat(),
                "created_at": item.created_at.isoformat(),
                "updated_at": item.updated_at.isoformat(),
                "published_at": item.published_at.isoformat() if item.published_at else None,
                "locked_until": item.locked_until.isoformat() if item.locked_until else None,
                "locked_by": item.locked_by,
                "reconciliation_attempts": item.reconciliation_attempts,
                "last_reconciled_at": item.last_reconciled_at.isoformat() if item.last_reconciled_at else None,
                "reconciliation_error": item.reconciliation_error,
            }
            self._db.table("publish_outbox").upsert(row).execute()
        except Exception as exc:
            if self._is_production():
                raise RuntimeError(f"Durable publish outbox persistence failed: {exc}") from exc
            logger.debug("outbox db save soft: %s", exc)

    def list_for_workspace(self, workspace_id: str, limit: int = 50) -> List[OutboxItem]:
        """Tenant-scoped listing only."""
        with self._lock:
            rows = [i for i in self._mem.values() if i.workspace_id == workspace_id]
        rows.sort(key=lambda x: x.created_at, reverse=True)
        return rows[:limit]

    def get(self, item_id: str, workspace_id: Optional[str] = None) -> Optional[OutboxItem]:
        with self._lock:
            item = self._mem.get(item_id)
        if item is None and self._db:
            try:
                res = self._db.table("publish_outbox").select("*").eq("id", item_id).maybe_single().execute()
                if res and res.data:
                    item = self._from_row(res.data)
                    with self._lock:
                        self._mem[item.id] = item
            except Exception:
                pass
        if item and workspace_id and item.workspace_id != workspace_id:
            return None  # isolation
        return item

    def _from_row(self, row: dict) -> OutboxItem:
        def parse(k):
            v = row.get(k)
            if isinstance(v, str):
                try:
                    return datetime.fromisoformat(v.replace("Z", "+00:00"))
                except Exception:
                    return datetime.now(timezone.utc)
            return v or datetime.now(timezone.utc)

        return OutboxItem(
            id=row["id"],
            workspace_id=row["workspace_id"],
            idempotency_key=row.get("idempotency_key") or ("pub_" + hashlib.sha256("|".join([row["workspace_id"], row.get("decision_id") or "", row.get("platform") or "facebook", row.get("brand_id") or ""]).encode("utf-8")).hexdigest()),
            decision_id=row["decision_id"],
            automation_job_id=row.get("automation_job_id"),
            correlation_id=row.get("correlation_id") or "",
            brand_id=row.get("brand_id"),
            platform=row.get("platform") or "facebook",
            caption=row.get("caption"),
            topic=row.get("topic"),
            risk_level=row.get("risk_level") or "low",
            payload=row.get("payload") or {},
            status=row.get("status") or "pending",
            attempts=int(row.get("attempts") or 0),
            max_attempts=int(row.get("max_attempts") or 5),
            last_error=row.get("last_error"),
            platform_post_id=row.get("platform_post_id"),
            available_at=parse("available_at"),
            created_at=parse("created_at"),
            updated_at=parse("updated_at"),
            published_at=parse("published_at") if row.get("published_at") else None,
            locked_until=parse("locked_until") if row.get("locked_until") else None,
            locked_by=row.get("locked_by"),
            reconciliation_attempts=int(row.get("reconciliation_attempts") or 0),
            last_reconciled_at=parse("last_reconciled_at") if row.get("last_reconciled_at") else None,
            reconciliation_error=row.get("reconciliation_error"),
            consecutive_not_found=int(row.get("consecutive_not_found") or 0),
        )

    def claim_batch(self, limit: int = 10) -> List[OutboxItem]:
        """Atomically claim work from durable storage when configured.

        Production never claims from process memory: a restart must not lose or
        duplicate work. Local development may use the in-memory fallback.
        """
        now = datetime.now(timezone.utc)
        if self._db is not None:
            try:
                res = self._db.rpc("claim_publish_outbox", {
                    "p_limit": int(limit),
                    "p_lease_seconds": 300,
                }).execute()
                rows = res.data or []
                claimed = []
                for row in rows:
                    item = self._from_row(row)
                    with self._lock:
                        self._mem[item.id] = item
                    claimed.append(item)
                return claimed
            except Exception as exc:
                logger.error("Durable outbox claim failed: %s", exc)
                if self._is_production():
                    return []

        claimed = []
        with self._lock:
            candidates = [
                i for i in self._mem.values()
                if i.status == "pending"
                and i.attempts < i.max_attempts
                and i.available_at <= now
            ]
            candidates.sort(key=lambda x: x.available_at)
            for item in candidates[:limit]:
                item.status = "processing"
                item.attempts += 1
                item.updated_at = now
                claimed.append(item)
        for item in claimed:
            self._save(item)
        return claimed

    def _finalize_durable(self, item: OutboxItem, *, status: str, error: Optional[str] = None, post_id: Optional[str] = None) -> bool:
        """Finalize only if this worker still owns the durable lease."""
        if self._db is None:
            return True
        rpc = ("complete_publish_outbox" if status == "published" else
               "quarantine_publish_outbox" if status == "uncertain" else
               "fail_publish_outbox")
        args = {
            "p_id": item.id,
            "p_locked_by": item.locked_by,
            "p_status": status,
            "p_error": error,
            "p_platform_post_id": post_id,
            "p_retry_delay_seconds": min(300, 5 * max(1, item.attempts)),
        }
        try:
            res = self._db.rpc(rpc, args).execute()
            rows = res.data or []
            return bool(rows[0].get("updated", False)) if isinstance(rows, list) and rows and isinstance(rows[0], dict) else bool(rows)
        except Exception as exc:
            if self._is_production():
                logger.error("Durable outbox finalization failed id=%s: %s", item.id, exc)
                return False
            logger.debug("outbox durable finalization soft: %s", exc)
            return True

    def reconcile(self, item_id: str, workspace_id: str, *, resolution: str, platform_post_id: Optional[str] = None, note: str = "") -> Optional[OutboxItem]:
        """Resolve an ambiguous publish with an explicit operator decision.

        ``confirmed_not_published`` re-queues the item. ``published`` records
        the provider post id. No automatic reconciliation is attempted.
        """
        item = self.get(item_id, workspace_id=workspace_id)
        if item is None or item.status != "uncertain":
            return None
        if resolution == "confirmed_not_published":
            item.status = "pending"
            item.available_at = datetime.now(timezone.utc)
            item.last_error = note[:500] or "Operator confirmed no external side effect"
        elif resolution == "published":
            if not platform_post_id:
                raise ValueError("platform_post_id is required when resolving as published")
            item.status = "published"
            item.platform_post_id = platform_post_id
            item.published_at = datetime.now(timezone.utc)
            item.last_error = note[:500] or None
        else:
            raise ValueError("resolution must be confirmed_not_published or published")
        item.locked_until = None; item.locked_by = None
        self._save(item)
        return item

    def reconcile_with_connector(self, item_id: str, workspace_id: str, connector) -> Optional[OutboxItem]:
        """Attempt authoritative provider reconciliation for one uncertain item.

        This method deliberately refuses to infer publication from captions,
        timestamps, HTTP errors, or fuzzy searches. A provider connector must
        expose ``lookup_post`` and return a conclusive result.
        """
        item = self.get(item_id, workspace_id=workspace_id)
        if item is None or item.status != "uncertain":
            return None
        from services.external_reconciliation import ExternalReconciler
        now = datetime.now(timezone.utc)
        item.reconciliation_attempts += 1
        item.last_reconciled_at = now
        result = ExternalReconciler().reconcile(item, connector=connector)
        item.reconciliation_error = result.evidence
        if result.status == "published":
            item.status = "published"
            item.platform_post_id = result.post_id or item.platform_post_id
            item.published_at = now
            item.last_error = None
            item.consecutive_not_found = 0
        elif result.status == "not_found":
            # Providers can be eventually consistent. Require two consecutive
            # authoritative misses before allowing a retry; a single 404 must
            # never turn an ambiguous side effect into a duplicate action.
            item.consecutive_not_found += 1
            if item.consecutive_not_found >= 2:
                item.status = "pending"
                item.available_at = now + timedelta(seconds=min(300, 15 * item.consecutive_not_found))
                item.last_error = "Provider confirmed no external object after repeated lookup"
            else:
                item.status = "uncertain"
                item.last_error = "Provider returned no object once; awaiting confirmation"
        else:
            item.status = "uncertain"
        item.locked_until = None
        item.locked_by = None
        self._save(item)
        return item

    def reconcile_from_provider_event(
        self,
        *,
        platform: str,
        provider_post_id: str,
        provider_status: str,
        workspace_id: Optional[str] = None,
        evidence: str = "",
    ) -> Optional[OutboxItem]:
        """Apply an authoritative provider webhook to an uncertain outbox item.

        Webhooks are accepted only when they identify the exact provider object
        already associated with an outbox intent.  Caption/time matching is
        deliberately forbidden because it can resolve the wrong publication.
        """
        platform = (platform or "").strip().lower()
        provider_post_id = (provider_post_id or "").strip()
        status = (provider_status or "").strip().lower()
        if not platform or not provider_post_id:
            return None

        item = None
        with self._lock:
            for candidate in self._mem.values():
                if candidate.platform != platform or candidate.platform_post_id != provider_post_id:
                    continue
                if workspace_id and candidate.workspace_id != workspace_id:
                    continue
                if candidate.status in {"uncertain", "processing"}:
                    item = candidate
                    break

        if item is None and self._db is not None:
            try:
                q = self._db.table("publish_outbox").select("*").eq("platform", platform).eq("platform_post_id", provider_post_id)
                if workspace_id:
                    q = q.eq("workspace_id", workspace_id)
                res = q.order("updated_at", desc=True).limit(1).execute()
                if res and res.data:
                    candidate = self._from_row(res.data[0])
                    if candidate.status in {"uncertain", "processing"}:
                        item = candidate
                        with self._lock:
                            self._mem[item.id] = item
            except Exception as exc:
                if self._is_production():
                    logger.error("Provider webhook reconciliation lookup failed: %s", exc)
                    return None
                logger.debug("Provider webhook reconciliation lookup failed: %s", exc)

        if item is None:
            return None

        now = datetime.now(timezone.utc)
        item.reconciliation_attempts += 1
        item.last_reconciled_at = now
        item.reconciliation_error = evidence[:500] if evidence else None

        if status in {"sent", "delivered", "read", "published", "finished"}:
            item.status = "published"
            item.published_at = item.published_at or now
            item.last_error = None
            item.consecutive_not_found = 0
        elif status in {"failed", "error", "rejected"}:
            item.status = "failed"
            item.last_error = (evidence or "Provider webhook reported publication failure")[:500]
        else:
            # Unknown provider states must never be promoted to success.
            item.status = "uncertain"
            item.last_error = (evidence or f"Provider webhook status '{status}' is not terminal")[:500]

        item.locked_until = None
        item.locked_by = None
        self._save(item)
        return item

    def _finalize_scheduled_post(self, item: OutboxItem, status: str, error: Optional[str] = None) -> None:
        """Reconcile the durable scheduled_posts intent with the outbox outcome."""
        scheduled_post_id = (item.payload or {}).get("scheduled_post_id")
        if not scheduled_post_id or not self._db:
            return
        try:
            update = {"status": status}
            if status == "published":
                update["completed_at"] = datetime.now(timezone.utc).isoformat()
            elif error:
                update["error"] = str(error)[:500]
            self._db.table("scheduled_posts").update(update).eq("id", scheduled_post_id).in_("status", ["processing", "outbox_pending"]).execute()
        except Exception as exc:
            logger.error("Scheduled post finalization failed id=%s status=%s: %s", scheduled_post_id, status, exc)

    # ── Item processing ──────────────────────────────────────────────

    async def process_item(self, item: OutboxItem) -> bool:
        """Execute one claimed outbox item and reconcile its AutomationJob."""
        from services.publish_orchestration import run_publish

        async def job_transition(status):
            if not item.automation_job_id:
                return
            try:
                from services.job_service import JobService
                from models.automation_job import JobStatus
                js = JobService(supabase_client=self._db)
                job = await js.find_by_id(item.automation_job_id)
                if job:
                    await js.transition(job, status)
            except Exception as exc:
                logger.error("Outbox job transition failed job=%s status=%s: %s", item.automation_job_id, status, exc)

        try:
            from models.automation_job import JobStatus
            await job_transition(JobStatus.PUBLISHING)
            payload = dict(item.payload or {})
            media_url = payload.get("media_url")
            image_bytes = None
            encoded = payload.get("image_bytes_b64")
            if encoded:
                try:
                    image_bytes = base64.b64decode(encoded)
                except Exception as exc:
                    raise RuntimeError(f"Invalid persisted media payload: {exc}")

            result = await run_publish(
                container=self._container,
                workspace_id=item.workspace_id,
                brand_id=item.brand_id,
                platform=item.platform,
                topic=item.topic or "",
                caption=item.caption or "",
                dry_run=False,
                source="outbox_drainer",
                risk_level=item.risk_level,
                decision_id=item.decision_id,
                durable=False,
                idempotency_key=item.idempotency_key,
                _from_outbox=True,
                image_url=media_url,
                image_bytes=image_bytes,
            )

            receipt = self._extract_receipt(result)

            # ── Receipt contract at the outbox boundary ───────────
            # Only a real post ID justifies `published`. Anything that claims
            # success without one is honestly downgraded to `uncertain`.
            if receipt["status"] == "ok" and receipt["platform_post_id"]:
                item.status = "published"
                item.platform_post_id = receipt["platform_post_id"]
                item.published_at = datetime.now(timezone.utc)
                item.last_error = None
                # Persist receipt metadata (page/account/mode) for audit
                item.payload = {
                    **(item.payload or {}),
                    "page_id": receipt.get("page_id"),
                    "account_id": receipt.get("account_id"),
                    "mode": receipt.get("mode"),
                    "receipt": {
                        "post_id": receipt["platform_post_id"],
                        "page_id": receipt.get("page_id"),
                        "account_id": receipt.get("account_id"),
                    },
                }
                if not self._finalize_durable(item, status="published", post_id=item.platform_post_id):
                    item.status = "uncertain"
                    item.last_error = "Durable finalization lost worker lease; reconcile before retry"
                    with self._lock:
                        self._mem[item.id] = item
                    return False
                self._finalize_scheduled_post(item, "published")
                item.locked_until = None
                item.locked_by = None
                self._save(item)
                if item.automation_job_id:
                    try:
                        from services.job_service import JobService
                        js = JobService(supabase_client=self._db)
                        job = await js.find_by_id(item.automation_job_id)
                        if job:
                            await js.mark_published(job, post_id=item.platform_post_id, result=result)
                    except Exception as exc:
                        logger.error("Outbox job publish reconciliation failed: %s", exc)
                return True

            if receipt["status"] == "ok" and not receipt["platform_post_id"]:
                # Fabricated success — downgrade to uncertain explicitly.
                logger.warning(
                    "Outbox: publish reported success without platform_post_id id=%s — "
                    "classifying uncertain",
                    item.id,
                )
                receipt["status"] = "uncertain"
                receipt["reconciliation_required"] = True
                if not receipt.get("error"):
                    receipt["error"] = "Publish reported success without a post ID"

            # ── Non-success: uncertain or failed ─────────────────
            if receipt["status"] == "uncertain" or receipt.get("reconciliation_required"):
                item.status = "uncertain"
                item.last_error = str(receipt.get("error") or "External side effect uncertain")[:500]
                # Preserve receipt metadata even for ambiguous outcomes — the
                # reconciliation worker needs page_id/account_id to look up.
                item.payload = {
                    **(item.payload or {}),
                    "page_id": receipt.get("page_id"),
                    "account_id": receipt.get("account_id"),
                    "mode": receipt.get("mode") or "live",
                }
                if not self._finalize_durable(item, status="uncertain", error=item.last_error):
                    item.status = "uncertain"
                    item.last_error = "Durable uncertain finalization unavailable; reconcile before retry"
                    with self._lock:
                        self._mem[item.id] = item
                    return False
                self._finalize_scheduled_post(item, "uncertain", item.last_error)
                item.locked_until = None
                item.locked_by = None
                self._save(item)
                if item.automation_job_id:
                    try:
                        from services.job_service import JobService
                        js = JobService(supabase_client=self._db)
                        job = await js.find_by_id(item.automation_job_id)
                        if job:
                            await js.mark_failed(
                                job,
                                error=item.last_error or "publish uncertain",
                                error_code="PUBLISH_UNCERTAIN",
                                retryable=False,
                            )
                    except Exception as exc:
                        logger.error("Outbox job uncertain reconciliation failed: %s", exc)
                return False

            # Explicit failure
            item.status = "failed"
            item.last_error = str(receipt.get("error") or "publish failed")[:500]
            if not self._finalize_durable(item, status=item.status, error=item.last_error):
                item.status = "uncertain"
                item.last_error = "Durable failure finalization unavailable; reconcile before retry"
                with self._lock:
                    self._mem[item.id] = item
                return False
            self._finalize_scheduled_post(item, "failed", item.last_error)
            item.locked_until = None
            item.locked_by = None
            self._save(item)
            if item.automation_job_id:
                try:
                    from services.job_service import JobService
                    js = JobService(supabase_client=self._db)
                    job = await js.find_by_id(item.automation_job_id)
                    if job:
                        await js.mark_failed(
                            job,
                            error=item.last_error or "publish failed",
                            error_code="PUBLISH_FAILED",
                            retryable=item.status != "dead",
                        )
                except Exception as exc:
                    logger.error("Outbox job failure reconciliation failed: %s", exc)
            return False

        except Exception as exc:
            # A timeout/transport exception is inherently ambiguous for an
            # external publish. Quarantine until an operator/provider lookup
            # establishes whether the side effect occurred.
            item.status = "uncertain"
            item.last_error = str(exc)[:500]
            if not self._finalize_durable(item, status=item.status, error=item.last_error):
                item.status = "uncertain"
                item.last_error = "Durable exception finalization unavailable; reconcile before retry"
                with self._lock:
                    self._mem[item.id] = item
            else:
                self._finalize_scheduled_post(item, "uncertain", item.last_error)
                item.locked_until = None
                item.locked_by = None
                self._save(item)
            logger.warning("Outbox process failed id=%s err=%s", item.id, exc)
            return False

    async def drain_once(self, limit: int = 10) -> Dict[str, int]:
        batch = self.claim_batch(limit=limit)
        ok = fail = 0
        for item in batch:
            if await self.process_item(item):
                ok += 1
            else:
                fail += 1
        return {"claimed": len(batch), "published": ok, "failed": fail}

    async def start_drainer(self, interval_seconds: float = 5.0) -> None:
        if self._running:
            return
        self._running = True

        async def loop():
            while self._running:
                try:
                    stats = await self.drain_once()
                    if stats["claimed"]:
                        logger.info("Outbox drain: %s", stats)
                except Exception as exc:
                    logger.debug("Outbox drain soft: %s", exc)
                await asyncio.sleep(interval_seconds)

        self._drainer_task = asyncio.create_task(loop(), name="publish-outbox-drainer")
        logger.info("Publish outbox drainer started")

    async def stop_drainer(self) -> None:
        self._running = False
        if self._drainer_task:
            self._drainer_task.cancel()
            try:
                await self._drainer_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            self._drainer_task = None


publish_outbox = PublishOutbox()


def enqueue(**kwargs):
    """Canonical module-level enqueue API used by the action pipeline."""
    return publish_outbox.enqueue(**kwargs)


_outbox_singleton = None

def get_publish_outbox():
    global _outbox_singleton
    if _outbox_singleton is None:
        _outbox_singleton = PublishOutbox()
    return _outbox_singleton
