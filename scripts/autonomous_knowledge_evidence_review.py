"""Bounded unattended sweep for the KDO knowledge evidence review queue.

Run inside the backend process or manually with the backend's normal environment variables:
    python -m scripts.autonomous_knowledge_evidence_review

The job is idempotent and tenant-scoped. It never rewrites existing verified
entries and never promotes a case unless the autonomous reviewer approves it.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from urllib.parse import urlparse
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from services.autonomous_knowledge_evidence_reviewer import (
    AutonomousKnowledgeEvidenceReviewer,
    build_source_snapshot_attestation,
    resolve_trusted_source_class,
    verify_source_snapshot_attestation,
)

logger = logging.getLogger("kdo.autonomous_knowledge_evidence_review")
ACTOR = "service:kdo-autonomous-knowledge-review"
PAGE_SIZE = 100


def _client():
    from services.supabase import supabase_service
    client = getattr(supabase_service, "service_client", None)
    if client is None:
        raise RuntimeError("Supabase service-role client is unavailable")
    return client


def _entry(row: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        id=str(row["id"]),
        content=str(row.get("content") or ""),
        workspace_id=row.get("workspace_id"),
        brand_id=row.get("brand_id"),
        doc_type=row.get("doc_type") or "text",
        metadata=row.get("metadata") if isinstance(row.get("metadata"), dict) else {},
    )


async def _capture_trusted_source_snapshot(client, row: dict[str, Any]) -> dict[str, Any]:
    """Fetch and attest an allowlisted source for historical entries, fail-closed."""
    entry = _entry(row)
    metadata = dict(entry.metadata)
    secret = os.getenv("KDO_KB_SOURCE_SNAPSHOT_HMAC_SECRET", "")
    if verify_source_snapshot_attestation(metadata, str(entry.workspace_id or ""), secret):
        return metadata

    source_url = metadata.get("source_url") or metadata.get("sourceUrl")
    if not isinstance(source_url, str) or not source_url:
        return metadata
    parsed = urlparse(source_url)
    if (
        parsed.scheme not in {"http", "https"} or not parsed.hostname
        or parsed.username or parsed.password or any(char.isspace() for char in source_url)
    ):
        return metadata
    digest = hashlib.sha256(entry.content.encode("utf-8")).hexdigest()
    prior_case = (
        client.table("knowledge_evidence_review_cases").select("review_status")
        .eq("knowledge_entry_id", entry.id).eq("content_sha256", digest).limit(1).execute()
    )
    if prior_case.data and prior_case.data[0].get("review_status") == "approved":
        return metadata
    try:
        trust_class = resolve_trusted_source_class(source_url)
        from services.web_intelligence.base import validate_external_url
        allowed, _reason = await asyncio.to_thread(validate_external_url, source_url)
        if not allowed or not secret:
            return metadata
        from services.web_intelligence import get_web_intelligence, init_web_intelligence
        web = get_web_intelligence() or init_web_intelligence()
        page = await web.fetch_async(source_url)
        source_text = str(getattr(page, "text", "") or "").strip()
        status_code = getattr(page, "status_code", None)
        if (
            getattr(page, "error", None) or not status_code
            or not 200 <= int(status_code) < 300
            or not source_text or len(source_text) > 30000
        ):
            return metadata

        source_id = str(metadata.get("source_id") or metadata.get("sourceId") or "")
        if not source_id or source_id.startswith("unverified-entry:"):
            source_id = "web-source:" + hashlib.sha256(
                f"{entry.workspace_id}|{source_url}".encode("utf-8")
            ).hexdigest()
        attestation = build_source_snapshot_attestation(
            secret=secret,
            workspace_id=str(entry.workspace_id),
            source_id=source_id,
            source_url=source_url,
            trust_class=trust_class,
            snapshot_text=source_text,
        )
        updated_metadata = {
            **metadata,
            **attestation,
            "source_id": source_id,
            "source_url": source_url,
            "source_title": str(getattr(page, "title", "") or metadata.get("source_title") or "")[:500],
            "source_type": metadata.get("source_type") or entry.doc_type,
            "trust_class": trust_class,
            "ingestion_method": "server_side_web_fetch",
        }
        saved = (
            client.table("knowledge_entries").update({"metadata": updated_metadata})
            .eq("id", entry.id).eq("workspace_id", entry.workspace_id)
            .eq("brand_id", entry.brand_id).select("id").execute()
        )
        if not saved.data:
            logger.warning("source_snapshot_persist_not_confirmed entry_id=%s", entry.id)
            return metadata
        return updated_metadata
    except Exception as exc:
        logger.info(
            "trusted_source_capture_skipped entry_id=%s reason=%s",
            entry.id, type(exc).__name__,
        )
        return metadata


def _case_for_entry(client, row: dict[str, Any]) -> dict[str, Any] | None:
    entry = _entry(row)
    metadata = entry.metadata
    digest = hashlib.sha256(entry.content.encode("utf-8")).hexdigest()
    found = (
        client.table("knowledge_evidence_review_cases").select("*")
        .eq("knowledge_entry_id", entry.id).eq("content_sha256", digest)
        .limit(1).execute()
    )
    if found.data:
        existing = found.data[0]
        prior_evidence = existing.get("review_evidence") if isinstance(existing.get("review_evidence"), dict) else {}
        prior_snapshot_digest = prior_evidence.get("source_snapshot_sha256")
        current_snapshot_digest = metadata.get("source_snapshot_sha256")
        if (
            existing.get("review_status") in {"quarantined", "rejected", "superseded"}
            and metadata.get("source_snapshot_text")
            and current_snapshot_digest
            and current_snapshot_digest != prior_snapshot_digest
        ):
            source_id = str(
                metadata.get("source_id") or metadata.get("sourceId")
                or existing.get("source_id") or f"unverified-entry:{entry.id}"
            ).strip()
            source_url = metadata.get("source_url") or metadata.get("sourceUrl")
            reopened = (
                client.table("knowledge_evidence_review_cases").update({
                    "review_status": "pending",
                    "reviewed_by": ACTOR,
                    "reviewed_at": datetime.now(timezone.utc).isoformat(),
                    "review_notes": "Trusted source snapshot became available; autonomous re-review queued.",
                    "source_id": source_id,
                    "source_url": source_url,
                    "source_title": metadata.get("source_title") or metadata.get("title"),
                    "source_type": metadata.get("source_type") or entry.doc_type,
                    "trust_class": metadata.get("trust_class"),
                }).eq("id", existing["id"]).eq("review_status", "quarantined")
                .select("*").single().execute()
            )
            return reopened.data
        if existing.get("review_status") == "pending" and metadata.get("source_snapshot_text"):
            source_id = str(metadata.get("source_id") or metadata.get("sourceId") or existing.get("source_id"))
            updated = (
                client.table("knowledge_evidence_review_cases").update({
                    "source_id": source_id,
                    "source_url": metadata.get("source_url") or metadata.get("sourceUrl"),
                    "source_title": metadata.get("source_title") or metadata.get("title"),
                    "source_type": metadata.get("source_type") or entry.doc_type,
                    "trust_class": metadata.get("trust_class"),
                }).eq("id", existing["id"]).eq("review_status", "pending")
                .select("*").single().execute()
            )
            return updated.data or existing
        return existing

    source_id = str(
        metadata.get("source_id") or metadata.get("sourceId")
        or f"unverified-entry:{entry.id}"
    ).strip()
    source_url = metadata.get("source_url") or metadata.get("sourceUrl")
    if not isinstance(source_url, str):
        source_url = None
    case_data = {
        "knowledge_entry_id": entry.id,
        "workspace_id": entry.workspace_id,
        "brand_id": entry.brand_id,
        "source_id": source_id,
        "submitted_by": ACTOR,
        "source_url": source_url,
        "source_title": metadata.get("source_title") or metadata.get("title"),
        "source_type": metadata.get("source_type") or entry.doc_type,
        "trust_class": metadata.get("trust_class"),
        "claim_scope": "general_knowledge_support",
        "content_sha256": digest,
        "metadata_snapshot": metadata,
        "review_status": "pending",
        "review_mode": "autonomous",
    }
    try:
        inserted = client.table("knowledge_evidence_review_cases").insert(case_data).select("*").single().execute()
        return inserted.data
    except Exception:
        # Concurrent ingestion or another worker may have inserted the unique
        # (knowledge_entry_id, content_sha256) case. Re-read; never duplicate.
        raced = (
            client.table("knowledge_evidence_review_cases").select("*")
            .eq("knowledge_entry_id", entry.id).eq("content_sha256", digest)
            .limit(1).execute()
        )
        return raced.data[0] if raced.data else None


async def _review_case(client, case: dict[str, Any], row: dict[str, Any]) -> str:
    entry = _entry(row)
    if (
        not entry.workspace_id or not entry.brand_id
        or str(entry.workspace_id).lower() in {"default", "unbound", "platform"}
    ):
        decision, confidence, reason, evidence = (
            "quarantined", 0.0, "A concrete workspace and brand binding are required", {}
        )
        claims = []
    else:
        assessment = await AutonomousKnowledgeEvidenceReviewer().review(entry=entry, case=case)
        decision = assessment.get("decision")
        if decision == "quarantine":
            decision = "quarantined"
        if decision not in {"approved", "rejected", "quarantined"}:
            decision = "quarantined"
        confidence = assessment.get("confidence", 0.0)
        reason = str(assessment.get("reason") or "Autonomous review completed")
        evidence = assessment.get("evidence") or {}
        claims = assessment.get("claims") or []

    changed = (
        client.table("knowledge_evidence_review_cases").update({
            "review_status": decision,
            "reviewed_by": ACTOR,
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
            "review_notes": reason[:4000],
            "review_mode": "autonomous",
            "review_confidence": confidence,
            "review_evidence": {
                "claims": claims,
                **evidence,
                "source_snapshot_sha256": (
                    entry.metadata.get("source_snapshot_sha256")
                    if isinstance(entry.metadata, dict) else None
                ),
            },
        }).eq("id", case["id"]).eq("review_status", "pending").select("*").execute()
    )
    if not changed.data:
        return "raced"

    if decision == "approved":
        # The promotion RPC is idempotent. Repeated sweeps safely retry a
        # previous transient promotion failure without repeating the review.
        client.rpc(
            "promote_approved_knowledge_evidence",
            {"p_case_id": case["id"], "p_actor_id": ACTOR},
        ).execute()
    return decision


async def _run() -> dict[str, int]:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    client = _client()
    maximum = max(1, min(1000, int(os.getenv("KDO_KB_REVIEW_BATCH_SIZE", "100"))))
    scan_limit = max(maximum, min(5000, maximum * 5))
    counters = {"scanned": 0, "discovered": 0, "approved": 0, "rejected": 0, "quarantined": 0, "raced": 0, "skipped": 0}

    state = (
        client.table("knowledge_evidence_review_sweep_state").select("last_entry_id")
        .eq("state_key", "global").limit(1).execute()
    )
    cursor = (state.data or [{}])[0].get("last_entry_id")
    last_scanned = cursor
    reached_end = False

    while counters["scanned"] < scan_limit and counters["discovered"] < maximum:
        take = min(PAGE_SIZE, scan_limit - counters["scanned"])
        query = (
            client.table("knowledge_entries").select(
                "id,content,workspace_id,brand_id,doc_type,metadata"
            ).not_.is_("workspace_id", "null")
        )
        if last_scanned:
            query = query.gt("id", last_scanned)
        result = query.order("id").range(0, take - 1).execute()
        rows = result.data or []
        if not rows:
            reached_end = True
            break

        for row in rows:
            last_scanned = str(row["id"])
            counters["scanned"] += 1
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            if metadata.get("verified") is True:
                counters["skipped"] += 1
                continue
            if not row.get("workspace_id") or not row.get("brand_id"):
                counters["skipped"] += 1
                continue

            captured_metadata = await _capture_trusted_source_snapshot(client, row)
            if captured_metadata != metadata:
                row = {**row, "metadata": captured_metadata}
            case = _case_for_entry(client, row)
            if not case:
                counters["skipped"] += 1
                continue
            status = case.get("review_status")
            if status == "pending":
                counters["discovered"] += 1
                outcome = await _review_case(client, case, row)
                if outcome in counters:
                    counters[outcome] += 1
            elif status == "approved":
                client.rpc(
                    "promote_approved_knowledge_evidence",
                    {"p_case_id": case["id"], "p_actor_id": ACTOR},
                ).execute()
                counters["skipped"] += 1
            else:
                counters["skipped"] += 1

            if counters["discovered"] >= maximum or counters["scanned"] >= scan_limit:
                break

        if len(rows) < take:
            reached_end = True
            break

    next_cursor = None if reached_end else last_scanned
    client.table("knowledge_evidence_review_sweep_state").upsert({
        "state_key": "global",
        "last_entry_id": next_cursor,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }, on_conflict="state_key").execute()

    counters["reached_end"] = int(reached_end)
    logger.info("Autonomous knowledge evidence sweep complete: %s", counters)
    return counters

if __name__ == "__main__":
    asyncio.run(_run())
