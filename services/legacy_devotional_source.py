"""Read-only, integrity-checked access to the pinned New Nature devotional dataset.

This module deliberately does not create evidence attestations or assign a trust
class. It only resolves a KDO legacy record to bytes from an immutable Git blob.
Rights review, source classification, claim adjudication and promotion remain
separate governed steps.
"""
from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

REPOSITORY = "OpenChristianData/open-christian-data"
COMMIT = "a09bef70799ca6bea6199e48fa4bd14c1d723a06"
PATH = "data/devotionals/spurgeons-morning-evening/morning-evening.json"
BLOB_SHA = "bdab95a7791ebe7bdfca5e752ede707b63337d7c"
MAX_BLOB_BYTES = 2_500_000
MAX_RECORD_BYTES = 30_000
SOURCE_ID_PREFIX = f"github-blob:{REPOSITORY}:{COMMIT}:{BLOB_SHA}"


class PinnedDatasetError(ValueError):
    """The pinned dataset or requested record failed integrity validation."""


def _git_blob_sha(data: bytes) -> str:
    header = b"blob " + str(len(data)).encode("ascii") + bytes([0]);
    return hashlib.sha1(header + data).hexdigest()


def decode_pinned_blob_response(response: dict[str, Any]) -> bytes:
    """Decode GitHub's Git-blob API payload and verify its immutable object ID."""
    if not isinstance(response, dict) or response.get("sha") != BLOB_SHA:
        raise PinnedDatasetError("pinned_blob_identity_mismatch")
    if response.get("encoding") != "base64":
        raise PinnedDatasetError("unexpected_blob_encoding")
    encoded = response.get("content")
    if not isinstance(encoded, str) or len(encoded) > ((MAX_BLOB_BYTES + 2) // 3) * 4 + 16_000:
        raise PinnedDatasetError("blob_response_too_large")
    # GitHub may wrap base64 in whitespace; remove only whitespace, then
    # require a strict alphabet/padding check instead of silently discarding
    # arbitrary invalid characters.
    try:
        raw = base64.b64decode("".join(encoded.split()), validate=True)
    except (ValueError, TypeError) as exc:
        raise PinnedDatasetError("invalid_blob_base64") from exc
    if not raw or len(raw) > MAX_BLOB_BYTES:
        raise PinnedDatasetError("blob_size_out_of_bounds")
    if _git_blob_sha(raw) != BLOB_SHA:
        raise PinnedDatasetError("git_blob_sha_mismatch")
    return raw


def parse_pinned_dataset(raw: bytes) -> list[dict[str, Any]]:
    """Parse the expected 732-row JSON array without coercing malformed rows."""
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_BLOB_BYTES:
        raise PinnedDatasetError("dataset_size_out_of_bounds")
    if _git_blob_sha(raw) != BLOB_SHA:
        raise PinnedDatasetError("git_blob_sha_mismatch")
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PinnedDatasetError("dataset_json_invalid") from exc
    if (
        not isinstance(document, dict)
        or not isinstance(document.get("meta"), dict)
        or document["meta"].get("id") != "spurgeons-morning-evening"
        or not isinstance(document.get("data"), list)
        or len(document["data"]) != 732
    ):
        raise PinnedDatasetError("dataset_schema_or_row_count_mismatch")
    rows = document["data"]
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise PinnedDatasetError("dataset_row_not_object")
        record_id = row.get("entry_id")
        blocks = row.get("content_blocks")
        if not isinstance(record_id, str) or not record_id.strip() or record_id in seen:
            raise PinnedDatasetError("dataset_record_id_invalid_or_duplicate")
        if not isinstance(blocks, list) or not blocks:
            raise PinnedDatasetError("dataset_content_blocks_missing")
        if not isinstance(row.get("primary_reference"), (str, dict, list, type(None))):
            raise PinnedDatasetError("dataset_primary_reference_invalid")
        seen.add(record_id)
    return rows


def extract_record_snapshot(rows: list[dict[str, Any]], record_id: str) -> dict[str, str]:
    """Return a deterministic, bounded record snapshot and its pinned identity."""
    if not isinstance(record_id, str) or not record_id.strip():
        raise PinnedDatasetError("record_id_required")
    matches = [row for row in rows if row.get("entry_id") == record_id]
    if len(matches) != 1:
        raise PinnedDatasetError("record_not_found_or_ambiguous")
    row = matches[0]
    snapshot = json.dumps(
        {
            "entry_id": row["entry_id"],
            "content_blocks": row["content_blocks"],
            "primary_reference": row.get("primary_reference"),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    encoded = snapshot.encode("utf-8")
    if len(encoded) > MAX_RECORD_BYTES:
        raise PinnedDatasetError("record_snapshot_too_large")
    return {
        "source_id": f"{SOURCE_ID_PREFIX}:record:{record_id}",
        "source_url": (
            f"https://github.com/{REPOSITORY}/blob/{COMMIT}/{PATH}"
        ),
        "snapshot_text": snapshot,
        "snapshot_sha256": hashlib.sha256(encoded).hexdigest(),
        "dataset_blob_sha": BLOB_SHA,
        "dataset_commit": COMMIT,
        "record_id": record_id,
    }



def build_attested_record_metadata(
    *,
    rows: list[dict[str, Any]],
    record_id: str,
    workspace_id: str,
    secret: str,
    trust_class: str,
) -> dict[str, str]:
    """Bind a recovered record to KDO's existing HMAC attestation contract.

    Trust classification is deliberately supplied by a separate, reviewed
    policy decision. This helper never infers or assigns it.
    """
    if not workspace_id or not secret:
        raise PinnedDatasetError("workspace_and_signing_secret_required")
    if trust_class not in {
        "government", "regulatory", "peer_reviewed", "academic",
        "official_documentation", "publisher_primary",
    }:
        raise PinnedDatasetError("explicit_allowed_trust_class_required")
    snapshot = extract_record_snapshot(rows, record_id)
    from services.autonomous_knowledge_evidence_reviewer import (
        build_source_snapshot_attestation,
    )

    attestation = build_source_snapshot_attestation(
        secret=secret,
        workspace_id=workspace_id,
        source_id=snapshot["source_id"],
        source_url=snapshot["source_url"],
        trust_class=trust_class,
        snapshot_text=snapshot["snapshot_text"],
    )
    return {
        **snapshot,
        **attestation,
        "source_id": snapshot["source_id"],
        "source_url": snapshot["source_url"],
        "trust_class": trust_class,
        "ingestion_method": "pinned_git_blob_record",
    }

def fetch_pinned_dataset() -> list[dict[str, Any]]:
    """Fetch only the exact GitHub blob endpoint; no redirects or arbitrary URLs."""
    import requests

    from services.web_intelligence.base import validate_external_url

    endpoint = f"https://api.github.com/repos/{REPOSITORY}/git/blobs/{BLOB_SHA}"
    allowed, reason = validate_external_url(endpoint)
    if not allowed:
        raise PinnedDatasetError(f"outbound_url_rejected:{reason}")
    response = requests.get(
        endpoint,
        timeout=(5, 20),
        allow_redirects=False,
        stream=True,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "KDO-Pinned-Knowledge-Source/1.0",
        },
    )
    try:
        if response.status_code != 200:
            raise PinnedDatasetError(f"github_blob_http_{response.status_code}")
        length = response.headers.get("Content-Length")
        if length:
            try:
                declared_length = int(length)
            except (TypeError, ValueError) as exc:
                raise PinnedDatasetError("blob_content_length_invalid") from exc
            if declared_length < 0:
                raise PinnedDatasetError("blob_content_length_invalid")
            if declared_length > 3_400_000:
                raise PinnedDatasetError("blob_response_too_large")
        chunks = bytearray()
        for chunk in response.iter_content(chunk_size=65_536):
            if chunk:
                chunks.extend(chunk)
                if len(chunks) > 3_400_000:
                    raise PinnedDatasetError("blob_response_too_large")
        try:
            payload = json.loads(chunks.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PinnedDatasetError("github_blob_response_invalid") from exc
        return parse_pinned_dataset(decode_pinned_blob_response(payload))
    finally:
        response.close()
