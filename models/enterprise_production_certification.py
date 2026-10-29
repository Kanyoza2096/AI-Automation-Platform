"""Canonical Phase 10 enterprise production certification contract."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CertificationStatus(str, Enum):
    CERTIFIED = "CERTIFIED"
    BLOCKED = "BLOCKED"
    NOT_PROVEN = "NOT_PROVEN"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


class CertificationScope(str, Enum):
    ARCHITECTURE = "ARCHITECTURE"
    SECURITY = "SECURITY"
    BUSINESS_OPERATION = "BUSINESS_OPERATION"
    AGENT_AUTONOMY = "AGENT_AUTONOMY"
    EXECUTION = "EXECUTION"
    RELIABILITY = "RELIABILITY"
    PROVENANCE = "PROVENANCE"
    RECOVERY = "RECOVERY"
    EXTERNAL_RUNTIME = "EXTERNAL_RUNTIME"


@dataclass(frozen=True)
class CertificationEvidence:
    evidence_id: str
    check_id: str
    status: str
    source: str
    observed_at: str | None = None
    fingerprint: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CertificationCheck:
    check_id: str
    scope: CertificationScope
    status: str
    required: bool = True
    evidence_ids: tuple[str, ...] = ()
    blocking_reason: str | None = None


@dataclass(frozen=True)
class EnterpriseProductionCertification:
    schema: str
    workspace_id: str
    status: CertificationStatus
    checks: tuple[CertificationCheck, ...]
    evidence: tuple[CertificationEvidence, ...]
    blocking_reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    fingerprint: str
    read_only: bool = True
    certifies_production: bool = False
    release_authority: str = "external_production_release_authority"
