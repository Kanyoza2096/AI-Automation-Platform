"""Diagnostician — symptom + evidence → probable cause + confidence."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from services.reliability.models import Diagnosis, Incident
from services.reliability.memory import IncidentMemory

logger = logging.getLogger("kanyoza.reliability.diagnostician")


class Diagnostician:
    def __init__(self, memory: Optional[IncidentMemory] = None):
        self._memory = memory or IncidentMemory()

    def diagnose(self, incident: Incident, evidence: Dict[str, Any]) -> Diagnosis:
        summary: List[str] = []
        related: List[str] = []
        cause = "unknown_degradation"
        confidence = 0.4

        circ = evidence.get("circulation") or {}
        meta = evidence.get("meta") or {}
        registry = evidence.get("health_registry") or {}

        # Circulation healthy vs Meta degraded — known false-positive pattern
        flow = circ.get("flow_score") if isinstance(circ, dict) else None
        meta_status = str(meta.get("status") or meta.get("overall") or "").lower()
        critical = int(meta.get("critical_alerts") or 0)
        active = int(meta.get("active_alerts") or 0)
        stale = int(meta.get("stale_engines") or 0)

        if flow is not None and float(flow) >= 95 and meta_status in ("degraded", "unhealthy"):
            summary.append(f"Circulation healthy (flow={flow})")
            summary.append(f"Meta reports {meta_status} (critical={critical}, active={active}, stale={stale})")
            if critical == 0 and active == 0:
                cause = "health_aggregation_inconsistency"
                confidence = 0.92
                related = ["meta_engine", "autonomous_loop", "nerve"]
                summary.append("No critical/active engine alerts — likely aggregation/noise")
            elif stale > 0 and critical == 0:
                cause = "stale_idle_engines_inflating_health"
                confidence = 0.88
                related = ["meta_engine"]
            else:
                cause = "engine_degradation"
                confidence = 0.75
                related = ["meta_engine"]
        elif critical > 0:
            cause = "offline_engines"
            confidence = 0.85
            related = ["meta_engine"]
            summary.append(f"{critical} offline/critical engine alert(s)")
        elif active > 0:
            cause = "degraded_engines"
            confidence = 0.8
            related = ["meta_engine"]
            summary.append(f"{active} degraded engine alert(s)")

        # Registry unhealthy services
        if isinstance(registry, dict) and "error" not in registry:
            bad = [
                n for n, r in registry.items()
                if isinstance(r, dict) and str(r.get("status", "")).lower() not in ("healthy", "nominal", "ok", "")
            ]
            if bad:
                summary.append(f"Health registry degraded: {', '.join(bad[:8])}")
                related.extend(bad[:5])
                if cause == "unknown_degradation":
                    cause = "service_health_degraded"
                    confidence = 0.7

        # Prefer domain evidence over a generic "service degraded" label.
        # JobService attaches a bounded read-only sample from automation_jobs
        # to its HealthReport.metadata; PulseCollector must preserve that metadata.
        job_report = registry.get("job_service") if isinstance(registry, dict) else None
        job_metadata = job_report.get("metadata") if isinstance(job_report, dict) else None
        failure_diagnostics = (
            job_metadata.get("failure_diagnostics")
            if isinstance(job_metadata, dict)
            else None
        )
        if (
            isinstance(failure_diagnostics, dict)
            and failure_diagnostics.get("status") == "available"
        ):
            failed_jobs = failure_diagnostics.get("jobs")
            if not isinstance(failed_jobs, list):
                failed_jobs = []
            try:
                total_failed = max(
                    0,
                    int(
                        failure_diagnostics.get("total_failed")
                        or failure_diagnostics.get("total_returned")
                        or 0
                    ),
                )
            except (TypeError, ValueError, OverflowError):
                total_failed = 0

            if total_failed > 0:
                categories = failure_diagnostics.get("counts_by_category")
                if not isinstance(categories, dict):
                    categories = {}
                category_summary = ", ".join(
                    f"{str(name)[:64]}={max(0, int(count))}"
                    for name, count in sorted(categories.items())
                    if isinstance(count, int) and not isinstance(count, bool) and count >= 0
                ) or "category evidence unavailable"
                step_codes = [
                    str(job.get("step_error_code") or "").strip().upper()
                    for job in failed_jobs
                    if isinstance(job, dict)
                ]
                validation_only = (
                    bool(failed_jobs)
                    and len(step_codes) == len(failed_jobs)
                    and not bool(failure_diagnostics.get("truncated"))
                    and all(code == "AI_VALIDATION_ERROR" for code in step_codes)
                )

                cause = (
                    "job_content_validation_backlog"
                    if validation_only
                    else "job_failure_backlog"
                )
                confidence = 0.95 if failed_jobs else 0.8
                related = ["job_service", "job_stats_service"]
                if validation_only:
                    related.extend(["content_engine", "post_workflow"])
                summary.append(
                    f"Persisted automation_jobs evidence: {total_failed} FAILED job(s)"
                    + (" (sample truncated)" if failure_diagnostics.get("truncated") else "")
                )
                summary.append(f"Failure categories: {category_summary}")
                if validation_only:
                    summary.append(
                        "Sampled failures are AI_VALIDATION_ERROR content/policy rejections, "
                        "not evidence of a stopped JobService process"
                    )

        # Symptom-specific overrides
        symptom = (incident.symptom or "").lower()
        if "workspace_id" in symptom or "nerve" in symptom or "legacy" in symptom:
            cause = "nerve_event_scope_mismatch"
            confidence = max(confidence, 0.9)
            related = list(set(related + ["nerve", "unified_bus"]))
            summary.append("Nerve legacy/unbound workspace events")

        if "stagnation" in symptom:
            cause = "activity_stagnation"
            confidence = max(confidence, 0.85)
            related = list(set(related + ["universal_pulse"]))

        if "token" in symptom or "190" in symptom or "auth" in symptom:
            cause = "connector_auth_failure"
            confidence = 0.9
            related = list(set(related + ["facebook", "connectors"]))

        similar = self._memory.find_similar(cause, limit=3)
        notes = ""
        if similar:
            notes = f"Similar past incidents: {', '.join(s['id'] for s in similar)}"
            # Boost confidence slightly when past remediation succeeded
            if any(s.get("outcome") == "success" for s in similar):
                confidence = min(0.98, confidence + 0.05)

        return Diagnosis(
            incident_id=incident.id,
            probable_cause=cause,
            confidence=confidence,
            evidence_summary=summary or ["Limited evidence available"],
            related_components=list(dict.fromkeys(related)),
            similar_incident_ids=[s["id"] for s in similar],
            notes=notes,
        )
