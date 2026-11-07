"""Canonical provenance envelope for read-only operational evidence.

The envelope attests to the backend component that produced the response and
fingerprints the exact response payload. It does not grant action authority.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any


PROVENANCE_SCHEMA = "KDOEvidenceProvenance/v1"


def attach_evidence_provenance(
    payload: dict[str, Any],
    *,
    source: str,
    authority: str,
    data_class: str,
) -> dict[str, Any]:
    """Return a response copy with truthful source, observation time and digest.

    observedAt is the time this backend component assembled the returned
    observation/projection. It is not a claim about the age of every underlying
    database row or external provider record.
    """
    if not isinstance(payload, dict):
        raise TypeError("evidence payload must be a dictionary")
    if not source.strip() or not authority.strip():
        raise ValueError("evidence source and authority are required")
    if data_class not in {"OBSERVED", "DERIVED"}:
        raise ValueError("data_class must be OBSERVED or DERIVED")

    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    fingerprint = "sha256:" + hashlib.sha256(canonical).hexdigest()
    return {
        **payload,
        "evidenceProvenance": {
            "schema": PROVENANCE_SCHEMA,
            "source": source,
            "authority": authority,
            "dataClass": data_class,
            "observedAt": datetime.now(timezone.utc).isoformat(),
            "evidenceFingerprint": fingerprint,
        },
    }
