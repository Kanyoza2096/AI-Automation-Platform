"""Deterministic lightweight checks for legacy and newly ingested KB entries.

This is an integrity/quality screen, not a source adjudicator. It never marks
knowledge as verified and never authorizes empirical claims or publishing.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import urlparse

_ALLOWED_TRUST_CLASSES = {
    "government", "regulatory", "peer_reviewed", "academic",
    "official_documentation", "publisher_primary",
}
_PLACEHOLDER_PATTERNS = (
    re.compile(r"\blorem ipsum\b", re.IGNORECASE),
    re.compile(r"\b(?:TODO|TBD|PLACEHOLDER)\b", re.IGNORECASE),
)


def check_knowledge_entry(content: str, metadata: Any = None) -> dict[str, Any]:
    """Return a reproducible check report without making evidence-trust claims."""
    text = content if isinstance(content, str) else ""
    meta = metadata if isinstance(metadata, dict) else {}
    issues: list[str] = []
    warnings: list[str] = []

    if not text.strip():
        issues.append("empty_content")
    if len(text) > 30000:
        issues.append("content_too_long")
    if any(pattern.search(text) for pattern in _PLACEHOLDER_PATTERNS):
        issues.append("placeholder_content")

    source_url = meta.get("source_url") or meta.get("sourceUrl")
    source_url_valid = False
    if source_url:
        if isinstance(source_url, str):
            parsed = urlparse(source_url.strip())
            source_url_valid = (
                parsed.scheme == "https"
                and bool(parsed.hostname)
                and not parsed.username
                and not parsed.password
                and not any(char.isspace() for char in source_url)
            )
        if not source_url_valid:
            warnings.append("source_url_not_valid_https")
    else:
        warnings.append("source_url_missing")

    source_id = str(meta.get("source_id") or meta.get("sourceId") or "").strip()
    if not source_id or source_id.startswith("unverified-entry:"):
        warnings.append("stable_source_id_missing")

    trust_class = str(meta.get("trust_class") or "").strip()
    if trust_class and trust_class not in _ALLOWED_TRUST_CLASSES:
        warnings.append("trust_class_unrecognized")
    elif not trust_class:
        warnings.append("trust_class_missing")

    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return {
        "schema_version": 1,
        "status": "needs_attention" if issues else "checked",
        "content_sha256": digest,
        "content_characters": len(text),
        "issues": issues,
        "warnings": warnings,
        "source_url_present": bool(source_url),
        "source_url_valid_https": source_url_valid,
        "stable_source_id_present": bool(source_id and not source_id.startswith("unverified-entry:")),
        "trust_class_recognized": bool(trust_class in _ALLOWED_TRUST_CLASSES),
        "verified": False,
        "approval_granted": False,
        "publishing_evidence_authorized": False,
    }
