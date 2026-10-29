"""Phase 5.7 — AI Fabric receipt boundary.

AI providers produce durable receipts. Successful receipts may become KDO-owned
interpretation evidence; the provider never creates a Decision or ActionIntent.
Failed/uncertain receipts remain provider evidence only and cannot enter the
successful interpretation path.

v1.1 (2026-10-22) — FINISH_REASON / FINGERPRINT PROJECTION:
  AIReceipt gained a finish_reason field (see services/ai_provider.py).
  receipt_fingerprint now hashes a fixed projection of receipt.to_dict()
  that excludes finish_reason, so existing rows in ai_receipts (written
  before the field existed, serialized without the key) and new rows
  (serialized with the key) both project to the same material before
  hashing. Without this projection, adding the field would change every
  historical fingerprint and break get_receipt(), AIReceiptReplayVerifier,
  and IntelligenceAIChainRecoveryRepository.capture/recover against every
  pre-v1.1 receipt.

  get_receipt now propagates finish_reason from the stored JSON back onto
  the reconstructed AIReceipt. Pre-v1.1 rows produce finish_reason=None.

  Why finish_reason is excluded from the fingerprint: the fingerprint
  identifies what request produced what response. finish_reason describes
  how the response terminated — it is response metadata, not response
  identity. Two receipts with identical outputs and different finish
  reasons are the same receipt.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Mapping

from services.ai_provider import AIReceipt, AIUsage
from services.intelligence_evidence_graph import IntelligenceEvidenceGraph

SCHEMA_VERSION = "KDO.IntelligenceAIFabric/v1"


# Fields that describe *what was asked* and *what was returned*. Excludes
# finish_reason (and any future diagnostic-only field), which describes
# *how the response terminated* — response metadata, not response identity.
# Keeping finish_reason out of this set is what makes the v1.1 field
# addition backwards compatible with existing ai_receipts rows.
_FINGERPRINT_FIELDS = (
    "status",
    "provider",
    "model",
    "raw_output",
    "structured_output",
    "usage",
    "correlation_id",
    "request_fingerprint",
    "error",
)


def receipt_fingerprint(receipt: AIReceipt) -> str:
    """SHA256 of the semantically invariant projection of the receipt.

    The projection intentionally excludes finish_reason (v1.1+) so that
    pre-v1.1 receipts (stored without the field) and post-v1.1 receipts
    (stored with the field) project to the same material and produce the
    same hash. This is the mechanism that preserves replay compatibility
    across the schema addition.

    Changing _FINGERPRINT_FIELDS changes every future fingerprint. Do not
    add or remove fields casually: any change here requires a coordinated
    migration of stored receipt_fingerprint values in ai_receipts and any
    AIChainManifest rows that reference them.
    """
    value = receipt.to_dict()
    projected = {k: value[k] for k in _FINGERPRINT_FIELDS if k in value}
    return hashlib.sha256(
        json.dumps(projected, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def interpretation_semantic_fingerprint(
    *, receipt: AIReceipt, workspace_id: str, signal_node_id: str | None = None
) -> str:
    """Fingerprint the deterministic KDO-owned interpretation projection of a receipt.

    The signal node identity is intentionally excluded from the semantic material so
    the fingerprint describes the interpretation content, not its graph placement.

    Note: ai_interpretation_semantic_fingerprint is computed over the
    output-side material plus the ai_receipt_fingerprint. Because
    receipt_fingerprint excludes finish_reason, the semantic fingerprint
    is stable across the v1.1 field addition. Stored provenance on
    existing interpretation nodes remains verifiable.
    """
    material = {
        "workspace_id": workspace_id,
        "payload": {
            "ai_output": receipt.structured_output,
            "raw_output": receipt.raw_output,
            "ai_status": receipt.status,
        },
        "provenance": {
            "ai_receipt_fingerprint": receipt_fingerprint(receipt),
            "ai_provider": receipt.provider,
            "ai_model": receipt.model,
            "ai_correlation_id": receipt.correlation_id,
            "ai_request_fingerprint": receipt.request_fingerprint,
            "ai_status": receipt.status,
            "ai_authority": "none",
            "ai_can_authorize": False,
            "ai_can_execute": False,
        },
    }
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class AIReceiptEvidence:
    receipt_fingerprint: str
    provider: str
    model: str
    correlation_id: str
    request_fingerprint: str
    status: str
    workspace_id: str
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class AIReceiptRepository:
    """Append-only durable AI receipt journal with workspace isolation."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS ai_receipts (
                receipt_fingerprint TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                correlation_id TEXT NOT NULL,
                request_fingerprint TEXT NOT NULL,
                status TEXT NOT NULL,
                receipt_json TEXT NOT NULL,
                schema_version TEXT NOT NULL
            )"""
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_receipts_workspace ON ai_receipts(workspace_id)")
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def append(self, receipt: AIReceipt, *, workspace_id: str) -> AIReceiptEvidence:
        if not workspace_id.strip():
            raise ValueError("workspace_id is required")
        fp = receipt_fingerprint(receipt)
        evidence = AIReceiptEvidence(
            receipt_fingerprint=fp,
            provider=receipt.provider,
            model=receipt.model,
            correlation_id=receipt.correlation_id,
            request_fingerprint=receipt.request_fingerprint,
            status=receipt.status,
            workspace_id=workspace_id,
        )
        with self._conn:
            self._conn.execute(
                """INSERT INTO ai_receipts
                (receipt_fingerprint,workspace_id,provider,model,correlation_id,request_fingerprint,status,receipt_json,schema_version)
                VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(receipt_fingerprint) DO NOTHING""",
                (fp, workspace_id, receipt.provider, receipt.model, receipt.correlation_id,
                 receipt.request_fingerprint, receipt.status,
                 json.dumps(receipt.to_dict(), sort_keys=True), SCHEMA_VERSION),
            )
        return evidence

    def get_receipt(self, fingerprint: str, *, workspace_id: str) -> AIReceipt:
        row = self._conn.execute(
            "SELECT * FROM ai_receipts WHERE receipt_fingerprint=? AND workspace_id=?",
            (fingerprint, workspace_id),
        ).fetchone()
        if row is None:
            raise PermissionError("AI receipt is absent or outside workspace scope")
        value = json.loads(row["receipt_json"])
        usage = value.get("usage")
        receipt = AIReceipt(
            status=value["status"], provider=value["provider"], model=value["model"],
            raw_output=value["raw_output"], structured_output=value.get("structured_output"),
            usage=AIUsage(**usage) if usage else None,
            correlation_id=value["correlation_id"], request_fingerprint=value["request_fingerprint"],
            error=value.get("error"),
            # v1.1 — None for pre-v1.1 rows (missing key), value otherwise.
            # The fingerprint projection above ignores this field, so old
            # rows verify cleanly and new rows preserve their signal.
            finish_reason=value.get("finish_reason"),
        )
        if receipt_fingerprint(receipt) != fingerprint:
            raise ValueError("stored AI receipt fingerprint mismatch")
        denormalized = {"provider": row["provider"], "model": row["model"], "correlation_id": row["correlation_id"], "request_fingerprint": row["request_fingerprint"], "status": row["status"]}
        actual = {"provider": receipt.provider, "model": receipt.model, "correlation_id": receipt.correlation_id, "request_fingerprint": receipt.request_fingerprint, "status": receipt.status}
        if denormalized != actual:
            raise ValueError("stored AI receipt metadata mismatch")
        return receipt

    def get(self, fingerprint: str, *, workspace_id: str) -> AIReceiptEvidence:
        row = self._conn.execute(
            "SELECT * FROM ai_receipts WHERE receipt_fingerprint=? AND workspace_id=?",
            (fingerprint, workspace_id),
        ).fetchone()
        if row is None:
            raise PermissionError("AI receipt is absent or outside workspace scope")
        receipt = self.get_receipt(fingerprint, workspace_id=workspace_id)
        return AIReceiptEvidence(
            receipt_fingerprint=fingerprint, provider=receipt.provider, model=receipt.model,
            correlation_id=receipt.correlation_id, request_fingerprint=receipt.request_fingerprint,
            status=receipt.status, workspace_id=workspace_id, schema_version=SCHEMA_VERSION,
        )


class AIReceiptReplayVerifier:
    """Validate historical interpretation provenance against the durable AI receipt."""

    def __init__(self, repository: AIReceiptRepository) -> None:
        self.repository = repository

    def verify_interpretation(
        self, *, graph: IntelligenceEvidenceGraph, interpretation_node_id: str, workspace_id: str,
    ) -> AIReceiptEvidence:
        node = graph.get_node(interpretation_node_id)
        if node.node_type != "interpretation":
            raise ValueError("AI receipt replay requires an interpretation node")
        if node.workspace_id != workspace_id:
            raise PermissionError("interpretation workspace does not match replay workspace")
        fp = str(node.provenance.get("ai_receipt_fingerprint") or "")
        if not fp:
            raise ValueError("interpretation is missing ai_receipt_fingerprint")
        receipt = self.repository.get_receipt(fp, workspace_id=workspace_id)
        expected = {
            "provider": receipt.provider, "model": receipt.model,
            "correlation_id": receipt.correlation_id,
            "request_fingerprint": receipt.request_fingerprint,
        }
        for key, value in expected.items():
            if node.provenance.get("ai_" + key) != value:
                raise ValueError(f"interpretation AI provenance mismatch: {key}")
        if node.provenance.get("ai_status") != receipt.status:
            raise ValueError("interpretation AI provenance mismatch: status")
        if receipt.status != "succeeded":
            raise ValueError("historical interpretation cannot be replayed from a non-successful AI receipt")
        expected_semantic = interpretation_semantic_fingerprint(
            receipt=receipt, workspace_id=workspace_id, signal_node_id=str(node.provenance.get("signal_node_id") or "")
        )
        actual_semantic = str(node.provenance.get("ai_interpretation_semantic_fingerprint") or "")
        if not actual_semantic:
            raise ValueError("interpretation is missing ai_interpretation_semantic_fingerprint")
        if actual_semantic != expected_semantic:
            raise ValueError("interpretation semantic fingerprint mismatch")
        expected_payload = {
            "ai_output": receipt.structured_output,
            "raw_output": receipt.raw_output,
            "ai_status": receipt.status,
        }
        if dict(node.payload) != expected_payload:
            raise ValueError("interpretation semantic payload mismatch")
        if node.provenance.get("ai_authority") != "none" or node.provenance.get("ai_can_authorize") is not False or node.provenance.get("ai_can_execute") is not False:
            raise ValueError("interpretation AI authority boundary is invalid")
        return self.repository.get(fp, workspace_id=workspace_id)


def ingest_ai_receipt_as_interpretation(
    *,
    graph: IntelligenceEvidenceGraph,
    repository: AIReceiptRepository,
    receipt: AIReceipt,
    workspace_id: str,
    signal_node_id: str,
) -> tuple[AIReceiptEvidence, Any | None]:
    """Persist receipt and, only for success, create KDO interpretation evidence."""
    evidence = repository.append(receipt, workspace_id=workspace_id)
    if receipt.status != "succeeded":
        return evidence, None

    signal = graph.get_node(signal_node_id)
    if signal.workspace_id != workspace_id:
        raise PermissionError("signal workspace does not match AI receipt workspace")

    payload = {
        "ai_output": receipt.structured_output,
        "raw_output": receipt.raw_output,
        "ai_status": receipt.status,
    }
    provenance = {
        "signal_node_id": signal_node_id,
        "ai_receipt_fingerprint": evidence.receipt_fingerprint,
        "ai_interpretation_semantic_fingerprint": interpretation_semantic_fingerprint(
            receipt=receipt, workspace_id=workspace_id, signal_node_id=signal_node_id
        ),
        "ai_provider": receipt.provider,
        "ai_model": receipt.model,
        "ai_correlation_id": receipt.correlation_id,
        "ai_request_fingerprint": receipt.request_fingerprint,
        "ai_status": receipt.status,
        "ai_authority": "none",
        "ai_can_authorize": False,
        "ai_can_execute": False,
    }
    node = graph.add_node(
        node_type="interpretation", workspace_id=workspace_id,
        payload=payload, provenance=provenance,
    )
    graph.link(signal_node_id, node.node_id, relation="supports")
    return evidence, node


__all__ = ["SCHEMA_VERSION", "AIReceiptEvidence", "AIReceiptRepository", "AIReceiptReplayVerifier", "receipt_fingerprint", "interpretation_semantic_fingerprint", "ingest_ai_receipt_as_interpretation"]
