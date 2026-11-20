"""Run deterministic lightweight checks over the legacy knowledge base.

Dry-run by default. Pass --apply to persist only the check report under
metadata.kdo_lightweight_review. This never sets metadata.verified and never
promotes evidence into publishing.
"""
from __future__ import annotations

import argparse
import json
import logging
from collections import Counter
from typing import Any

from services.lightweight_knowledge_checks import check_knowledge_entry

logger = logging.getLogger("kdo.lightweight_knowledge_review")


def _client():
    from services.supabase import supabase_service
    client = getattr(supabase_service, "service_client", None)
    if client is None:
        raise RuntimeError("Supabase service-role client is unavailable")
    return client


def run(*, apply: bool = False, batch_size: int = 100, maximum: int = 0, workspace_id: str | None = None) -> dict[str, Any]:
    if workspace_id is not None and not str(workspace_id).strip():
        raise ValueError("workspace_id must be a non-empty authenticated workspace ID")
    client = _client()
    offset = 0
    scanned = 0
    counts: Counter[str] = Counter()
    warnings: Counter[str] = Counter()
    write_attempted = 0
    write_confirmed = 0
    write_failed = 0
    while True:
        take = min(batch_size, maximum - scanned) if maximum else batch_size
        if take <= 0:
            break
        query = client.table("knowledge_entries").select("id,content,workspace_id,brand_id,metadata")
        if workspace_id is not None:
            query = query.eq("workspace_id", workspace_id)
        result = (
            query
            .order("id")
            .range(offset, offset + take - 1)
            .execute()
        )
        rows = result.data or []
        if not rows:
            break
        for row in rows:
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            report = check_knowledge_entry(row.get("content") or "", metadata)
            counts[report["status"]] += 1
            warnings.update(report["warnings"])
            if apply:
                write_attempted += 1
                updated = {
                    **metadata,
                    "kdo_lightweight_review": report,
                }
                query = (
                    client.table("knowledge_entries")
                    .update({"metadata": updated})
                    .eq("id", row["id"])
                    .eq("workspace_id", row["workspace_id"])
                )
                if row.get("brand_id") is not None:
                    query = query.eq("brand_id", row["brand_id"])
                saved = query.select("id").execute()
                if saved.data:
                    write_confirmed += 1
                else:
                    write_failed += 1
            scanned += 1
        offset += len(rows)
        if len(rows) < take or (maximum and scanned >= maximum):
            break
    return {
        "mode": "apply" if apply else "dry_run",
        "workspace_scoped": workspace_id is not None,
        "scanned": scanned,
        "counts": dict(counts),
        "warning_counts": dict(warnings),
        "write_attempted": write_attempted,
        "write_confirmed": write_confirmed,
        "write_failed": write_failed,
        "verified_entries_created": 0,
        "promotions_performed": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Persist check reports; never approve or promote.")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--maximum", type=int, default=0, help="Optional row limit; 0 scans all rows.")
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 500:
        parser.error("--batch-size must be between 1 and 500")
    if args.maximum < 0:
        parser.error("--maximum cannot be negative")
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(run(apply=args.apply, batch_size=args.batch_size, maximum=args.maximum), indent=2))


if __name__ == "__main__":
    main()
