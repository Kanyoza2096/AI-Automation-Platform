"""Bounded unattended sweep for the KDO knowledge evidence review queue.

Run from a Render Cron Job with the backend's normal environment variables:
    python -m scripts.autonomous_knowledge_evidence_review

The job is idempotent and tenant-scoped. It never rewrites existing verified
entries and never promotes a case unless the autonomous reviewer approves it.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from services.autonomous_knowledge_evidence_reviewer import (
    AutonomousKnowledgeEvidenceReviewer,
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
        return found.data[0]

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
            "review_evidence": {"claims": claims, **evidence},
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


def run() -> dict[str, int]:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    client = _client()
    maximum = max(1, min(1000, int(os.getenv("KDO_KB_REVIEW_BATCH_SIZE", "100"))))
    counters = {"discovered": 0, "approved": 0, "rejected": 0, "quarantined": 0, "raced": 0, "skipped": 0}

    offset = 0
    while counters["discovered"] < maximum:
        take = min(PAGE_SIZE, maximum - counters["discovered"])
        result = (
            client.table("knowledge_entries").select(
                "id,content,workspace_id,brand_id,doc_type,metadata"
            ).not_.is_("workspace_id", "null").range(offset, offset + take - 1).execute()
        )
        rows = result.data or []
        if not rows:
            break
        offset += len(rows)
        for row in rows:
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            if metadata.get("verified") is True:
                counters["skipped"] += 1
                continue
            if not row.get("workspace_id") or not row.get("brand_id"):
                counters["skipped"] += 1
                continue
            counters["discovered"] += 1
            case = _case_for_entry(client, row)
            if not case:
                counters["skipped"] += 1
                continue
            if case.get("review_status") == "pending":
                outcome = asyncio.run(_review_case(client, case, row))
                if outcome in counters:
                    counters[outcome] += 1
            elif case.get("review_status") == "approved":
                # Promotion retry is safe because the database RPC is idempotent.
                client.rpc(
                    "promote_approved_knowledge_evidence",
                    {"p_case_id": case["id"], "p_actor_id": ACTOR},
                ).execute()
                counters["approved"] += 1
            else:
                counters["skipped"] += 1
        if len(rows) < take:
            break

    logger.info("Autonomous knowledge evidence sweep complete: %s", counters)
    return counters


if __name__ == "__main__":
    run()
