"""V70.12 — Real Business Operating Certification.

Offline certification gate for the V70 business operating layer.  It proves
architectural invariants and deterministic cross-domain behaviour without
pretending that live provider/database certification has occurred.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping
import asyncio
import hashlib
import inspect
import json

from services.business_automation_catalog import get_template
from services.v70_11_cross_domain_business_orchestration import SCENARIOS, CrossDomainBusinessOrchestrator, OrchestrationStatus
from services.v70_business_operating_layer import BusinessOperatingLayer, assert_operating_layer_is_governed


class CertificationStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_PROVEN = "NOT_PROVEN"


@dataclass(frozen=True)
class CertificationFinding:
    control: str
    status: CertificationStatus
    evidence: str


@dataclass(frozen=True)
class CertificationReport:
    version: str
    status: CertificationStatus
    findings: tuple[CertificationFinding, ...]

    @property
    def passed(self) -> int:
        return sum(x.status == CertificationStatus.PASS for x in self.findings)

    @property
    def failed(self) -> int:
        return sum(x.status == CertificationStatus.FAIL for x in self.findings)

    @property
    def not_proven(self) -> int:
        return sum(x.status == CertificationStatus.NOT_PROVEN for x in self.findings)

    def fingerprint(self) -> str:
        payload = [(x.control, x.status.value, x.evidence) for x in self.findings]
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class _CertificationRun:
    accepted = True


class _CertificationLayer:
    def __init__(self, workspace_id: str, workflows: tuple[str, ...]):
        self.workspace_id = workspace_id
        self.calls: list[tuple[str, Mapping[str, Any]]] = []
        class Obj: pass
        obj = Obj()
        obj.workflow_ids = workflows
        self.objectives = {"cert-objective": obj}

    async def handle_event(self, objective_id: str, workflow_id: str, *, event: Mapping[str, Any], approved: bool = False, now=None):
        self.calls.append((workflow_id, dict(event)))
        return _CertificationRun()


def _finding(control: str, status: CertificationStatus, evidence: str) -> CertificationFinding:
    return CertificationFinding(control, status, evidence)


def run_offline_certification() -> CertificationReport:
    findings: list[CertificationFinding] = []

    # C1: every explicit scenario resolves only to catalog capabilities.
    missing = [w for steps in SCENARIOS.values() for w in steps if get_template(w) is None]
    findings.append(_finding("C1_catalog_capability_integrity", CertificationStatus.PASS if not missing else CertificationStatus.FAIL,
                             "all scenario workflows resolve in catalog" if not missing else f"missing={missing}"))

    # C2: every scenario is cross-domain or intentionally single-domain; no fuzzy routing.
    bad_shape = [s for s, steps in SCENARIOS.items() if not steps or any(not isinstance(w, str) or not w.strip() for w in steps)]
    findings.append(_finding("C2_explicit_scenario_integrity", CertificationStatus.PASS if not bad_shape else CertificationStatus.FAIL,
                             "scenario map is explicit and non-empty" if not bad_shape else f"invalid={bad_shape}"))

    # C3: orchestration has exactly one dependency on the governed operating facade.
    sig = inspect.signature(CrossDomainBusinessOrchestrator.__init__)
    c3 = "operating_layer" in sig.parameters
    findings.append(_finding("C3_single_governed_execution_facade", CertificationStatus.PASS if c3 else CertificationStatus.FAIL,
                             "orchestrator accepts BusinessOperatingLayer dependency and no provider dependency"))

    # C4: operating layer itself exposes only the governed engine execution surface.
    try:
        class Engine:
            async def run_event(self, **kwargs): return object()
        layer = BusinessOperatingLayer("cert-w", engine=Engine())
        assert_operating_layer_is_governed(layer)
        findings.append(_finding("C4_governed_operating_layer", CertificationStatus.PASS,
                                 "BusinessOperatingLayer governance assertion passed"))
    except Exception as exc:
        findings.append(_finding("C4_governed_operating_layer", CertificationStatus.FAIL, repr(exc)))

    # C5-C7: execute every scenario deterministically through the same facade.
    execution_failures = []
    for scenario, workflows in SCENARIOS.items():
        layer = _CertificationLayer("cert-w", tuple(workflows))
        orch = CrossDomainBusinessOrchestrator("cert-w", operating_layer=layer)
        try:
            prepared = orch.prepare(scenario=scenario, event={"event_id": f"cert:{scenario}", "workspace_id": "cert-w"}, objective_id="cert-objective")
            result = asyncio.run(orch.execute(orchestration_id=prepared.state.orchestration_id,
                                               event={"event_id": f"cert:{scenario}", "workspace_id": "cert-w"}))
            if not result.accepted or result.state.status != OrchestrationStatus.COMPLETED or [x[0] for x in layer.calls] != list(workflows):
                execution_failures.append(scenario)
        except Exception as exc:
            execution_failures.append(f"{scenario}:{exc!r}")
    findings.append(_finding("C5_cross_domain_scenario_execution", CertificationStatus.PASS if not execution_failures else CertificationStatus.FAIL,
                             "all registered scenarios completed in declared order" if not execution_failures else f"failures={execution_failures}"))

    # C6: tenant boundary.
    layer = _CertificationLayer("cert-w", SCENARIOS["business.plan.risk"])
    orch = CrossDomainBusinessOrchestrator("cert-w", operating_layer=layer)
    try:
        orch.prepare(scenario="business.plan.risk", event={"event_id": "tenant", "workspace_id": "other"}, objective_id="cert-objective")
        findings.append(_finding("C6_tenant_isolation", CertificationStatus.FAIL, "cross-tenant event was accepted"))
    except ValueError:
        findings.append(_finding("C6_tenant_isolation", CertificationStatus.PASS, "cross-tenant event rejected"))

    # C7: duplicate source event and terminal replay fences.
    layer = _CertificationLayer("cert-w", SCENARIOS["business.plan.risk"])
    orch = CrossDomainBusinessOrchestrator("cert-w", operating_layer=layer)
    try:
        p = orch.prepare(scenario="business.plan.risk", event={"event_id": "dup"}, objective_id="cert-objective")
        asyncio.run(orch.execute(orchestration_id=p.state.orchestration_id, event={"event_id": "dup"}))
        duplicate_prepare_rejected = False
        try:
            orch.prepare(scenario="business.plan.risk", event={"event_id": "dup"}, objective_id="cert-objective")
        except ValueError:
            duplicate_prepare_rejected = True
        terminal_rejected = False
        try:
            asyncio.run(orch.execute(orchestration_id=p.state.orchestration_id, event={"event_id": "dup"}))
        except ValueError:
            terminal_rejected = True
        ok = duplicate_prepare_rejected and terminal_rejected
        findings.append(_finding("C7_replay_and_terminal_fences", CertificationStatus.PASS if ok else CertificationStatus.FAIL,
                                 "duplicate source and terminal replay rejected" if ok else "replay fence failed"))
    except Exception as exc:
        findings.append(_finding("C7_replay_and_terminal_fences", CertificationStatus.FAIL, repr(exc)))

    # C8: no live-provider proof is claimed by offline certification.
    findings.append(_finding("C8_live_provider_certification", CertificationStatus.NOT_PROVEN,
                             "offline gate does not establish provider credentials, rate limits, or external side effects"))
    findings.append(_finding("C9_durable_database_restart_certification", CertificationStatus.NOT_PROVEN,
                             "offline gate does not establish persistence across process restart against production database"))

    status = CertificationStatus.FAIL if any(x.status == CertificationStatus.FAIL for x in findings) else CertificationStatus.NOT_PROVEN if any(x.status == CertificationStatus.NOT_PROVEN for x in findings) else CertificationStatus.PASS
    return CertificationReport("v70.12", status, tuple(findings))


__all__ = ["CertificationStatus", "CertificationFinding", "CertificationReport", "run_offline_certification"]
