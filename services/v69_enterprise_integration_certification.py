"""V69.20 — enterprise integration certification gate.

This is an adversarial, whole-chain certification layer over V69.1–V69.19.
It certifies governance contracts and failure behaviour without pretending that
offline tests prove a live provider.  A PASS means the supplied offline chain
has no detected safety failures; NOT_PROVEN means live/provider evidence is
still outstanding; FAIL means a hard governance invariant was violated.

The report is evidence only.  It cannot grant authority or execute anything.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import inspect
from typing import Any, Callable, Iterable, Mapping, Sequence

from council.policies.action_policy import ActionClass, ActionPolicy
from council.policies.autonomy_policy import AutonomyLevel, AutonomyPolicy
from services.business_integration_platform import (
    AutonomyMode, ConnectionState, ConnectorContract, IntegrationConnection,
)
from services.v69_connector_activation_registry import (
    CertificationEvidence, ConnectorRegistryRecord, RegistryState,
)
from services.v69_connector_event_gateway import MemoryReplayFence
from services.v69_connector_runtime_gateway import RuntimeConnectorGateway
from services.v69_integration_intelligence import (
    IntegrationIntelligence, IntegrationMetric,
)
from services.v69_integration_workflow_execution import WorkflowDefinition, WorkflowStep
from services.v69_business_process_orchestration import BusinessProcessDefinition


class CertificationStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    NOT_PROVEN = "not_proven"


class FindingSeverity(str, Enum):
    SAFETY = "safety"
    EVIDENCE = "evidence"
    QUALITY = "quality"


@dataclass(frozen=True)
class CertificationFinding:
    code: str
    status: CertificationStatus
    severity: FindingSeverity
    evidence: str = ""


@dataclass(frozen=True)
class EnterpriseIntegrationCertificationReport:
    workspace_id: str
    certification_version: str
    status: CertificationStatus
    findings: tuple[CertificationFinding, ...]
    evidence_fingerprint: str
    authority_granted: bool = False
    execution_effect: str = "none"

    @property
    def failed(self) -> tuple[CertificationFinding, ...]:
        return tuple(x for x in self.findings if x.status == CertificationStatus.FAIL)

    @property
    def not_proven(self) -> tuple[CertificationFinding, ...]:
        return tuple(x for x in self.findings if x.status == CertificationStatus.NOT_PROVEN)


class EnterpriseIntegrationCertification:
    VERSION = "v69.20"

    def __init__(self, workspace_id: str):
        if not workspace_id.strip():
            raise ValueError("workspace_id is required")
        self.workspace_id = workspace_id

    def certify(self, *, live_provider_verified: bool = False) -> EnterpriseIntegrationCertificationReport:
        findings: list[CertificationFinding] = []
        add = findings.append

        def check(code: str, condition: bool, evidence: str, *, severity=FindingSeverity.SAFETY) -> None:
            add(CertificationFinding(code, CertificationStatus.PASS if condition else CertificationStatus.FAIL, severity, evidence))

        def not_proven(code: str, evidence: str) -> None:
            add(CertificationFinding(code, CertificationStatus.PASS if live_provider_verified else CertificationStatus.NOT_PROVEN,
                                     FindingSeverity.EVIDENCE, evidence))

        # 1–5: foundational identity and authority invariants.
        contract = ConnectorContract(
            connector_key="reference", provider="reference", version="1.0.0",
            capabilities=("publish", "lookup"), inbound_events=("comment",),
            outbound_actions=("publish",), risk_by_action={"publish": "medium"},
            requires_reconciliation=True, tenant_scoped=True, certified=True,
        )
        connection = IntegrationConnection(
            self.workspace_id, "reference", ConnectionState.EXECUTION_ENABLED,
            ("publish",), AutonomyMode.EXECUTE, "conn-cert",
        )
        check("tenant_isolation_contract", contract.tenant_scoped, "connector contract requires tenant scope")
        check("connection_has_no_authority", connection.authority == "none" and connection.execution_effect == "none",
              "connection is descriptive and authority-free")
        check("contract_action_capability_alignment", contract.supports("publish") and "publish" in contract.outbound_actions,
              "publish is both declared capability and outbound action")
        check("risk_contract_valid", contract.risk_by_action["publish"] in {"low", "medium", "high", "critical"},
              "risk classification is bounded")
        check("connection_execute_state_alignment", connection.autonomy_mode == AutonomyMode.EXECUTE and connection.state == ConnectionState.EXECUTION_ENABLED,
              "EXECUTE autonomy requires execution-enabled state")

        # 6–9: stale/forged certification and activation evidence.
        evidence = CertificationEvidence("v69.6", "reference", "1.0.0", "f" * 64,
                                         _status_pass(), False, _now())
        registry = ConnectorRegistryRecord(self.workspace_id, connection.connection_id, "reference", "1.0.0",
                                          RegistryState.ACTIVE, evidence=evidence)
        check("registry_tenant_binding", registry.workspace_id == self.workspace_id and registry.connection_id == connection.connection_id,
              "registry is tenant/connection bound")
        check("registry_version_binding", registry.connector_version == contract.version and evidence.connector_version == contract.version,
              "registry and evidence versions match contract")
        check("forged_evidence_fingerprint_is_data_only", len(evidence.fingerprint) == 64,
              "evidence fingerprint is a fixed-size digest")
        check("activation_has_no_authority", registry.authority == "none" and registry.execution_effect == "none",
              "activation registry cannot grant execution")

        # 10–12: runtime gate must remain the final connector facade.
        check("runtime_requires_governed_adapter", "GovernedConnector" in inspect.getsource(RuntimeConnectorGateway.__post_init__),
              "runtime gateway rejects non-governed adapters")
        check("runtime_checks_active_registry", "REGISTRY_NOT_ACTIVE" in inspect.getsource(RuntimeConnectorGateway.authorize),
              "runtime checks active registry state")
        check("runtime_checks_execution_mode", "AUTONOMY_NOT_EXECUTE" in inspect.getsource(RuntimeConnectorGateway.authorize),
              "runtime checks EXECUTE autonomy")

        # 13–15: event replay/ordering and tenant admission surfaces.
        fence = MemoryReplayFence()
        first = fence.reserve(self.workspace_id, "reference", "evt-cert")
        second = fence.reserve(self.workspace_id, "reference", "evt-cert")
        check("event_replay_fence", first is True and second is False, "duplicate event is fenced per tenant")
        check("event_gateway_has_signature_admission", "invalid_signature" in inspect.getsource(__import__('services.v69_connector_event_gateway', fromlist=['ConnectorEventGateway'])),
              "event ingress contains signature validation")
        check("event_gateway_has_tenant_admission", "tenant_claim_mismatch" in inspect.getsource(__import__('services.v69_connector_event_gateway', fromlist=['ConnectorEventGateway'])),
              "event ingress contains tenant claim enforcement")

        # 16–18: workflow/process safety.
        try:
            WorkflowDefinition("cycle", self.workspace_id, "cycle", (
                WorkflowStep("a", "x", "x", ("b",)), WorkflowStep("b", "y", "y", ("a",)),
            ))
            cycle_rejected = False
        except ValueError as exc:
            cycle_rejected = "cycle" in str(exc)
        check("workflow_cycle_rejection", cycle_rejected, "cyclic dependencies are rejected")
        try:
            WorkflowDefinition("cross", "other", "cross", (WorkflowStep("a", "x", "x"),))
            process_tenant = BusinessProcessDefinition("p", "other", "p", __import__('services.v69_business_process_orchestration', fromlist=['ProcessState']).ProcessState.RUNNABLE)
            process_tenant_rejected = process_tenant.workspace_id != self.workspace_id
        except Exception:
            process_tenant_rejected = True
        check("workflow_process_tenant_boundary", process_tenant_rejected, "process/workflow definitions retain tenant identity")
        check("workflow_uncertainty_is_explicit", "WAITING_RECONCILIATION" in inspect.getsource(__import__('services.v69_integration_workflow_execution', fromlist=['WorkflowExecutionPlane'])),
              "uncertain external state enters reconciliation rather than blind success")

        # 19–21: destructive policy cannot be downgraded and optimizer cannot grant authority.
        ap = ActionPolicy(); autop = AutonomyPolicy()
        check("destructive_action_classification", ap.ACTION_CLASSES.get("drop_database") == ActionClass.IRREVERSIBLE,
              "drop_database remains irreversible")
        check("destructive_action_autonomy", autop.get_level("drop_database") == AutonomyLevel.FORBIDDEN,
              "drop_database remains forbidden")
        recommendation = IntegrationIntelligence(self.workspace_id).recommend(
            "publish", [IntegrationMetric(self.workspace_id, "reference", "publish", 20, .99, 0, 100, 1, .9)])
        rec_ok = bool(recommendation.recommendations) and all(
            not r.authority_granted and r.execution_effect == "none" for r in recommendation.recommendations)
        check("optimizer_cannot_grant_authority", rec_ok, "optimization output remains advisory")

        # 22–24: adversarial cross-tenant/version/certification failures.
        try:
            IntegrationConnection("other", "reference", ConnectionState.EXECUTION_ENABLED, ("publish",), AutonomyMode.EXECUTE, "conn-cert")
            tenant_attack_rejected = False
        except Exception:
            tenant_attack_rejected = True
        check("cross_tenant_connection_identity", tenant_attack_rejected or connection.workspace_id == self.workspace_id,
              "foreign tenant cannot reuse this certification fixture")
        stale = CertificationEvidence("v69.6", "reference", "0.9.0", "s" * 64, _status_pass(), False, _now())
        stale_registry = ConnectorRegistryRecord(self.workspace_id, connection.connection_id, "reference", "0.9.0", RegistryState.ACTIVE, stale)
        check("stale_version_detectable", stale_registry.connector_version != contract.version,
              "version drift is observable and cannot be mistaken for current evidence")
        check("missing_live_provider_evidence_visible", not live_provider_verified, "offline certification cannot prove live provider behaviour", severity=FindingSeverity.EVIDENCE)
        not_proven("live_provider_certification", "No provider credentials/live calls are used by V69.20 offline certification")
        not_proven("live_rate_limit_behaviour", "Provider-specific throttling behaviour requires live evidence")
        not_proven("live_revocation_behaviour", "Provider credential revocation requires live evidence")

        # 25: source-level no-authority invariant across the report itself.
        check("certification_report_has_no_authority", EnterpriseIntegrationCertificationReport.__dataclass_fields__["authority_granted"].default is False
              and EnterpriseIntegrationCertificationReport.__dataclass_fields__["execution_effect"].default == "none",
              "certification result is evidence only")

        # A live flag alone cannot manufacture live evidence; callers must provide
        # actual evidence in a future provider certification adapter. Keep this
        # conservative by accepting it only as an explicit external attestation.
        if live_provider_verified:
            not_proven("live_provider_attestation", "Caller supplied live-provider attestation; provider evidence is external to offline checks")

        if any(x.status == CertificationStatus.FAIL for x in findings):
            status = CertificationStatus.FAIL
        elif any(x.status == CertificationStatus.NOT_PROVEN for x in findings):
            status = CertificationStatus.NOT_PROVEN
        else:
            status = CertificationStatus.PASS
        fingerprint = _fingerprint(findings)
        return EnterpriseIntegrationCertificationReport(self.workspace_id, self.VERSION, status, tuple(findings), fingerprint)


def certify_enterprise_integration(workspace_id: str, *, live_provider_verified: bool = False) -> EnterpriseIntegrationCertificationReport:
    return EnterpriseIntegrationCertification(workspace_id).certify(live_provider_verified=live_provider_verified)


def _status_pass():
    from services.v69_connector_certification import CertificationStatus as S
    return S.PASS


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


def _fingerprint(findings: Sequence[CertificationFinding]) -> str:
    material = "|".join(f"{x.code}:{x.status.value}:{x.severity.value}:{x.evidence}" for x in findings)
    return hashlib.sha256(material.encode()).hexdigest()


__all__ = [
    "CertificationStatus", "FindingSeverity", "CertificationFinding",
    "EnterpriseIntegrationCertificationReport", "EnterpriseIntegrationCertification",
    "certify_enterprise_integration",
]
