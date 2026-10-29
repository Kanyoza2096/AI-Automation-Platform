"""R13-D evidence and clarification orchestration.

Chooses a safe evidence channel for each outstanding discovery requirement.
This layer plans requests only; it never promotes evidence to truth and never
executes external providers.
"""
from __future__ import annotations
from typing import Any

CHANNELS = ("clarify", "website", "document", "connected_source")


def _priority(requirement: str) -> str:
    text = requirement.lower()
    if any(x in text for x in ("policy", "rules", "terms", "procedure", "standards")):
        return "document"
    if any(x in text for x in ("pricing", "description", "services", "products", "hours", "channels")):
        return "website"
    if any(x in text for x in ("authoritative", "record", "data source", "inventory")):
        return "connected_source"
    return "clarify"


def orchestrate_evidence(*, requirements: list[str], supplied_urls: list[str] | None = None,
                         supplied_documents: list[str] | None = None,
                         connected_sources: list[str] | None = None) -> dict[str, Any]:
    urls = list(supplied_urls or [])
    docs = list(supplied_documents or [])
    sources = list(connected_sources or [])
    requests = []
    for req in requirements:
        channel = _priority(req)
        status = "requested"
        if channel == "website" and urls:
            status = "evidence_available"
        elif channel == "document" and docs:
            status = "evidence_available"
        elif channel == "connected_source" and sources:
            status = "source_available"
        requests.append({
            "requirement": req,
            "recommended_channel": channel,
            "status": status,
            "requires_explicit_confirmation": True,
            "creates_knowledge": False,
        })
    return {
        "schema": "KDO.R13D.EvidenceClarificationPlan/v1",
        "read_only": True,
        "requests": requests,
        "supplied_evidence": {"urls": urls, "documents": docs, "connected_sources": sources},
        "safety": {
            "does_not_promote_evidence_to_truth": True,
            "does_not_authorize_actions": True,
            "does_not_execute_providers": True,
        },
    }
