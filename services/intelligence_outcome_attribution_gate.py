"""Phase 5 — authoritative post-execution intelligence outcome attribution.

This module is deliberately read-only. It does not execute, authorize, retry, or
rewrite provider truth. It validates that a business outcome can be attributed
to an intelligence-originated execution only when the durable chain contains:

ActionIntent admission -> execution -> provider receipt -> authoritative
verification -> terminal reconciliation -> business outcome.

Intelligence provenance is lineage evidence only. Provider/verification/
reconciliation evidence remains authoritative for the external outcome.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping


class AttributionState:
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    UNCERTAIN = "UNCERTAIN"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()


def _get(row: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if row.get(key) is not None:
            return row.get(key)
    return None


@dataclass(frozen=True)
class AuthoritativeOutcomeAttribution:
    workspace_id: str
    recommendation_node_id: str
    action_intent_node_id: str
    admission_fingerprint: str
    execution_id: str
    provider_object_id: str | None
    verification_id: str
    reconciliation_id: str
    outcome_id: str
    state: str
    evidence_fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "recommendation_node_id": self.recommendation_node_id,
            "action_intent_node_id": self.action_intent_node_id,
            "admission_fingerprint": self.admission_fingerprint,
            "execution_id": self.execution_id,
            "provider_object_id": self.provider_object_id,
            "verification_id": self.verification_id,
            "reconciliation_id": self.reconciliation_id,
            "outcome_id": self.outcome_id,
            "state": self.state,
            "evidence_fingerprint": self.evidence_fingerprint,
            "causal_claim": False,
            "action_authority_granted": False,
            "authoritative_outcome_source": "verification_and_reconciliation",
        }


class IntelligenceOutcomeAttributionGate:
    """Validate, but never manufacture, authoritative outcome attribution."""

    VERSION = "phase5.2"

    def attribute(
        self,
        *,
        workspace_id: str,
        intelligence_provenance: Mapping[str, Any],
        execution: Mapping[str, Any],
        verification: Mapping[str, Any],
        reconciliation: Mapping[str, Any],
        outcome: Mapping[str, Any],
    ) -> AuthoritativeOutcomeAttribution:
        ws = str(workspace_id or "").strip()
        if not ws:
            raise ValueError("workspace_id is required")

        provenance = dict(intelligence_provenance or {})
        required_provenance = {
            "action_intent_node_id",
            "admission_fingerprint",
            "recommendation_node_id",
            "policy_id",
        }
        missing = sorted(k for k in required_provenance if not str(provenance.get(k) or "").strip())
        if missing:
            raise ValueError(f"intelligence provenance incomplete: {', '.join(missing)}")

        for name, row in (("execution", execution), ("verification", verification),
                          ("reconciliation", reconciliation), ("outcome", outcome)):
            if not isinstance(row, Mapping):
                raise ValueError(f"{name} evidence must be a mapping")
            row_ws = _get(row, "workspace_id", "workspaceId")
            if row_ws is not None and str(row_ws) != ws:
                raise PermissionError(f"{name} workspace mismatch")

        execution_id = str(_get(execution, "execution_id", "executionId") or "").strip()
        if not execution_id:
            raise ValueError("execution_id is required")
        execution_prov = _get(execution, "intelligence_provenance", "intelligenceProvenance") or {}
        if not isinstance(execution_prov, Mapping):
            raise ValueError("execution intelligence provenance is required")
        self._assert_provenance_match(provenance, execution_prov, "execution")

        receipt = _get(execution, "receipt") or {}
        if not isinstance(receipt, Mapping):
            raise ValueError("provider receipt is required")
        receipt_status = str(_get(receipt, "status") or _get(execution, "status") or "").lower()
        if receipt_status in {"uncertain", "in_progress"}:
            raise ValueError("uncertain provider receipt cannot produce authoritative attribution")
        provider_object_id = _get(receipt, "provider_object_id") or _get(execution, "provider_object_id")
        if receipt_status == "succeeded" and not provider_object_id:
            raise ValueError("successful receipt requires provider_object_id")

        verification_id = str(_get(verification, "verification_id", "verificationId", "id") or "").strip()
        verification_state = str(_get(verification, "state", "status", "verification_state") or "").lower()
        if not verification_id:
            raise ValueError("verification_id is required")
        if verification_state not in {"verified", "verified_provider", "verified_provider_terminal", "succeeded", "success"}:
            raise ValueError("authoritative verification is required before attribution")
        verification_exec = _get(verification, "execution_id", "executionId")
        if verification_exec is not None and str(verification_exec) != execution_id:
            raise ValueError("verification execution mismatch")
        verification_object = _get(verification, "provider_object_id", "providerObjectId", "provider_reference")
        if provider_object_id and verification_object and str(provider_object_id) != str(verification_object):
            raise ValueError("verification provider identity mismatch")

        reconciliation_id = str(_get(reconciliation, "reconciliation_id", "reconciliationId", "id") or "").strip()
        reconciliation_state = str(_get(reconciliation, "state", "status", "verification_state") or "").lower()
        terminal = bool(_get(reconciliation, "terminal"))
        if not reconciliation_id:
            raise ValueError("reconciliation_id is required")
        if not terminal or reconciliation_state not in {"terminal", "verified", "verified_provider_terminal", "succeeded", "failed"}:
            raise ValueError("terminal authoritative reconciliation is required before attribution")
        reconciliation_exec = _get(reconciliation, "execution_id", "executionId")
        if reconciliation_exec is not None and str(reconciliation_exec) != execution_id:
            raise ValueError("reconciliation execution mismatch")

        outcome_id = str(_get(outcome, "outcome_id", "outcomeId", "id", "observation_id") or "").strip()
        if not outcome_id:
            raise ValueError("outcome_id is required")
        outcome_exec = _get(outcome, "execution_id", "executionId")
        if outcome_exec is not None and str(outcome_exec) != execution_id:
            raise ValueError("outcome execution mismatch")
        outcome_reconciliation = _get(outcome, "reconciliation_id", "reconciliationId")
        if outcome_reconciliation is not None and str(outcome_reconciliation) != reconciliation_id:
            raise ValueError("outcome reconciliation mismatch")

        evidence = {
            "version": self.VERSION,
            "workspace_id": ws,
            "intelligence_provenance": provenance,
            "execution_id": execution_id,
            "provider_object_id": provider_object_id,
            "verification_id": verification_id,
            "reconciliation_id": reconciliation_id,
            "outcome_id": outcome_id,
            "authoritative_outcome_source": "verification_and_reconciliation",
        }
        state = "VERIFIED" if reconciliation_state in {"terminal", "verified", "verified_provider_terminal", "succeeded"} else "FAILED"
        return AuthoritativeOutcomeAttribution(
            workspace_id=ws,
            recommendation_node_id=str(provenance["recommendation_node_id"]),
            action_intent_node_id=str(provenance["action_intent_node_id"]),
            admission_fingerprint=str(provenance["admission_fingerprint"]),
            execution_id=execution_id,
            provider_object_id=str(provider_object_id) if provider_object_id else None,
            verification_id=verification_id,
            reconciliation_id=reconciliation_id,
            outcome_id=outcome_id,
            state=state,
            evidence_fingerprint=_fingerprint(evidence),
        )

    @staticmethod
    def _assert_provenance_match(expected: Mapping[str, Any], actual: Mapping[str, Any], source: str) -> None:
        for key in ("action_intent_node_id", "admission_fingerprint", "recommendation_node_id", "policy_id"):
            if str(actual.get(key) or "") != str(expected.get(key) or ""):
                raise ValueError(f"{source} intelligence provenance mismatch: {key}")


__all__ = ["AttributionState", "AuthoritativeOutcomeAttribution", "IntelligenceOutcomeAttributionGate"]
