"""Phase 10: one evidence-backed production certification view.

This service converges existing certification evidence. It does not execute,
authorize, deploy, activate providers, or replace subsystem certification.
Missing external proof remains NOT_PROVEN and therefore blocks certification.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from typing import Any, Iterable

from models.enterprise_production_certification import (
    CertificationCheck,
    CertificationEvidence,
    CertificationScope,
    CertificationStatus,
    EnterpriseProductionCertification,
)


REQUIRED_CHECKS: tuple[tuple[str, CertificationScope], ...] = (
    ("architecture_integrity", CertificationScope.ARCHITECTURE),
    ("security_controls", CertificationScope.SECURITY),
    ("business_operating_model", CertificationScope.BUSINESS_OPERATION),
    ("agent_control_plane", CertificationScope.AGENT_AUTONOMY),
    ("execution_governance", CertificationScope.EXECUTION),
    ("universal_provenance", CertificationScope.PROVENANCE),
    ("business_reliability", CertificationScope.RELIABILITY),
    ("recovery_readiness", CertificationScope.RECOVERY),
    ("external_runtime_evidence", CertificationScope.EXTERNAL_RUNTIME),
)


class EnterpriseProductionCertificationService:
    SCHEMA = "KDO.EnterpriseProductionCertification/v1"

    def assess(
        self,
        *,
        workspace_id: str,
        evidence: Iterable[dict[str, Any]],
    ) -> EnterpriseProductionCertification:
        normalized: list[dict[str, Any]] = []
        for item in evidence:
            if not isinstance(item, dict):
                continue
            if item.get("workspace_id") not in (None, workspace_id):
                continue
            normalized.append(dict(item))
        normalized.sort(key=lambda x: (str(x.get("check_id", "")), str(x.get("evidence_id", ""))))

        evidence_models = tuple(
            CertificationEvidence(
                evidence_id=str(item.get("evidence_id") or self._id("evidence", item)),
                check_id=str(item.get("check_id", "")),
                status=str(item.get("status", "NOT_PROVEN")).upper(),
                source=str(item.get("source", "unknown")),
                observed_at=item.get("observed_at"),
                fingerprint=item.get("fingerprint"),
                details=dict(item.get("details") or {}),
            )
            for item in normalized
            if item.get("check_id")
        )
        by_check: dict[str, list[CertificationEvidence]] = {}
        for item in evidence_models:
            by_check.setdefault(item.check_id, []).append(item)

        checks: list[CertificationCheck] = []
        blockers: list[str] = []
        warnings: list[str] = []
        for check_id, scope in REQUIRED_CHECKS:
            items = by_check.get(check_id, [])
            statuses = {item.status for item in items}
            if not items or statuses <= {"NOT_PROVEN"}:
                status = "NOT_PROVEN"
                reason = f"MISSING_OR_UNPROVEN:{check_id}"
                blockers.append(reason)
            elif "FAIL" in statuses:
                status = "FAIL"
                reason = f"FAILED:{check_id}"
                blockers.append(reason)
            elif "BLOCKED" in statuses:
                status = "BLOCKED"
                reason = f"BLOCKED:{check_id}"
                blockers.append(reason)
            elif "PASS" in statuses:
                status = "PASS"
                reason = None
            else:
                status = "NOT_PROVEN"
                reason = f"UNRECOGNIZED_EVIDENCE_STATUS:{check_id}"
                blockers.append(reason)
            if any(item.status not in {"PASS"} for item in items):
                warnings.append(f"NON_PASS_EVIDENCE:{check_id}")
            checks.append(CertificationCheck(
                check_id=check_id,
                scope=scope,
                status=status,
                required=True,
                evidence_ids=tuple(item.evidence_id for item in items),
                blocking_reason=reason,
            ))

        if any(c.status == "FAIL" for c in checks):
            status = CertificationStatus.FAILED
        elif blockers:
            status = CertificationStatus.BLOCKED
        else:
            status = CertificationStatus.CERTIFIED

        payload = {
            "schema": self.SCHEMA,
            "workspace_id": workspace_id,
            "status": status.value,
            "checks": [asdict(c) | {"scope": c.scope.value} for c in checks],
            "evidence": [asdict(e) for e in evidence_models],
            "blocking_reasons": sorted(set(blockers)),
            "warnings": sorted(set(warnings)),
        }
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return EnterpriseProductionCertification(
            schema=self.SCHEMA,
            workspace_id=workspace_id,
            status=status,
            checks=tuple(checks),
            evidence=evidence_models,
            blocking_reasons=tuple(sorted(set(blockers))),
            warnings=tuple(sorted(set(warnings))),
            fingerprint=fingerprint,
            certifies_production=status is CertificationStatus.CERTIFIED,
        )

    @staticmethod
    def _id(prefix: str, value: dict[str, Any]) -> str:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"))
        return f"{prefix}_{hashlib.sha256(raw.encode()).hexdigest()[:24]}"


__all__ = ["EnterpriseProductionCertificationService", "REQUIRED_CHECKS"]
