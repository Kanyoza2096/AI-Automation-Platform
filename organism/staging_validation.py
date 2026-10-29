"""Deterministic staging validation contracts and failure-injection harness.

This module deliberately uses injected probes rather than opening network
connections itself. A staging runner supplies authoritative adapters for DB,
Redis, provider APIs, Docker/runtime and the organism lifecycle. Failures are
reported as evidence; they never become health by default.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Iterable, Mapping, Optional


class ProbeStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNOBSERVABLE = "unobservable"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class ProbeResult:
    name: str
    status: ProbeStatus
    detail: str = ""
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class StagingValidationReport:
    environment: str
    results: tuple[ProbeResult, ...]
    failures: tuple[str, ...]
    unobservable: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.failures and not self.unobservable and bool(self.results)


@dataclass(frozen=True)
class ChaosScenario:
    name: str
    expected_behavior: str
    run: Callable[[], ProbeResult]


class StagingValidationHarness:
    """Run supplied staging probes and controlled chaos scenarios."""

    REQUIRED_PROBES = ("database_rls", "redis", "execution_fencing", "webhook_idempotency", "provider_adapter", "organism_lifecycle")

    def __init__(self, *, environment: str = "staging") -> None:
        env = str(environment).strip().lower()
        if env != "staging":
            raise ValueError("staging validation harness only permits environment='staging'")
        self.environment = env

    @staticmethod
    def _normalize(name: str, value: ProbeResult | bool | None) -> ProbeResult:
        if isinstance(value, ProbeResult):
            return value
        if value is True:
            return ProbeResult(name, ProbeStatus.PASS, "probe returned success")
        if value is False:
            return ProbeResult(name, ProbeStatus.FAIL, "probe returned failure")
        return ProbeResult(name, ProbeStatus.UNOBSERVABLE, "probe returned no authoritative result")

    def run(self, probes: Mapping[str, Callable[[], ProbeResult | bool | None]], *, chaos: Iterable[ChaosScenario] = ()) -> StagingValidationReport:
        results: list[ProbeResult] = []
        for name in self.REQUIRED_PROBES:
            probe = probes.get(name)
            if probe is None:
                results.append(ProbeResult(name, ProbeStatus.UNOBSERVABLE, "required probe not supplied"))
                continue
            try:
                results.append(self._normalize(name, probe()))
            except Exception as exc:
                results.append(ProbeResult(name, ProbeStatus.FAIL, f"probe raised {type(exc).__name__}: {exc}"))
        for scenario in chaos:
            try:
                result = scenario.run()
                if result.name != scenario.name:
                    result = ProbeResult(scenario.name, result.status, result.detail, result.evidence)
            except Exception as exc:
                result = ProbeResult(scenario.name, ProbeStatus.FAIL, f"chaos scenario raised {type(exc).__name__}: {exc}")
            results.append(result)
        failures = tuple(r.name for r in results if r.status is ProbeStatus.FAIL)
        unknown = tuple(r.name for r in results if r.status is ProbeStatus.UNOBSERVABLE)
        return StagingValidationReport(self.environment, tuple(results), failures, unknown)


def fail_closed_probe(name: str, operation: Callable[[], bool]) -> ProbeResult:
    """Adapter helper: exceptions become explicit failures, never success."""
    try:
        return ProbeResult(name, ProbeStatus.PASS if bool(operation()) else ProbeStatus.FAIL)
    except Exception as exc:
        return ProbeResult(name, ProbeStatus.FAIL, f"{type(exc).__name__}: {exc}")


__all__ = ["ProbeStatus", "ProbeResult", "StagingValidationReport", "ChaosScenario", "StagingValidationHarness", "fail_closed_probe"]
