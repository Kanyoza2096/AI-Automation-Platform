"""Machine-readable staging evidence and promotion reports.

Evidence is immutable, tenant-aware metadata about externally observed staging
checks. It is deliberately separate from application health and execution
authority: evidence can inform readiness but cannot authorize execution.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from typing import Any, Iterable, Mapping
from uuid import uuid4

from organism.staging_validation import ProbeResult, ProbeStatus, StagingValidationReport


class FailureClass(str, Enum):
    NONE = "none"
    DEPENDENCY = "dependency"
    AUTHORITY = "authority"
    TENANT_ISOLATION = "tenant_isolation"
    EXECUTION_UNCERTAINTY = "execution_uncertainty"
    TIMEOUT = "timeout"
    CONTRACT = "contract"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str
    correlation_id: str
    environment: str
    probe: str
    status: ProbeStatus
    failure_class: FailureClass
    detail: str
    evidence: tuple[str, ...]
    observed_at: str
    provenance: str
    digest: str

    @staticmethod
    def _digest(payload: Mapping[str, Any]) -> str:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @classmethod
    def from_probe(
        cls,
        probe: ProbeResult,
        *,
        environment: str,
        correlation_id: str,
        provenance: str = "staging_probe",
        failure_class: FailureClass | None = None,
        observed_at: datetime | None = None,
    ) -> "EvidenceRecord":
        ts = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        inferred = failure_class or (FailureClass.NONE if probe.status in (ProbeStatus.PASS, ProbeStatus.SKIPPED) else FailureClass.UNKNOWN)
        payload = {
            "correlation_id": correlation_id,
            "environment": environment,
            "probe": probe.name,
            "status": probe.status.value,
            "failure_class": inferred.value,
            "detail": probe.detail,
            "evidence": list(probe.evidence),
            "observed_at": ts,
            "provenance": provenance,
        }
        return cls(str(uuid4()), correlation_id, environment, probe.name, probe.status, inferred,
                   probe.detail, tuple(probe.evidence), ts, provenance, cls._digest(payload))

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "correlation_id": self.correlation_id,
            "environment": self.environment,
            "probe": self.probe,
            "status": self.status.value,
            "failure_class": self.failure_class.value,
            "detail": self.detail,
            "evidence": list(self.evidence),
            "observed_at": self.observed_at,
            "provenance": self.provenance,
            "digest": self.digest,
        }


@dataclass(frozen=True)
class PromotionReport:
    environment: str
    correlation_id: str
    generated_at: str
    evidence: tuple[EvidenceRecord, ...]
    promotable: bool
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "environment": self.environment,
            "correlation_id": self.correlation_id,
            "generated_at": self.generated_at,
            "evidence": [item.to_dict() for item in self.evidence],
            "promotable": self.promotable,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, indent=2)


class StagingEvidenceCollector:
    """Convert a validation report into immutable evidence and promotion output."""

    def collect(
        self,
        report: StagingValidationReport,
        *,
        correlation_id: str | None = None,
        provenance: str = "staging_validation_harness",
        failure_classes: Mapping[str, FailureClass] | None = None,
    ) -> PromotionReport:
        correlation = correlation_id or str(uuid4())
        classes = failure_classes or {}
        records = tuple(
            EvidenceRecord.from_probe(
                result,
                environment=report.environment,
                correlation_id=correlation,
                provenance=provenance,
                failure_class=classes.get(result.name),
            )
            for result in report.results
        )
        blockers = tuple(report.failures) + tuple(report.unobservable)
        warnings = tuple(r.name for r in report.results if r.status is ProbeStatus.SKIPPED)
        return PromotionReport(
            report.environment,
            correlation,
            datetime.now(timezone.utc).isoformat(),
            records,
            not blockers and bool(records),
            blockers,
            warnings,
        )


def verify_evidence(record: EvidenceRecord) -> bool:
    payload = {
        "correlation_id": record.correlation_id,
        "environment": record.environment,
        "probe": record.probe,
        "status": record.status.value,
        "failure_class": record.failure_class.value,
        "detail": record.detail,
        "evidence": list(record.evidence),
        "observed_at": record.observed_at,
        "provenance": record.provenance,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")).hexdigest() == record.digest


__all__ = ["FailureClass", "EvidenceRecord", "PromotionReport", "StagingEvidenceCollector", "verify_evidence"]
