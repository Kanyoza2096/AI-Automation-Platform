"""
KANYOZA DIRECTOR v3.3 — DIAGNOSTIC EXTENSION
"THE SYSTEM DIAGNOSTIC BRAIN"

Extends the base Director with system-wide diagnostic capabilities:
  1. SystemTopology — dependency graph of all platform components
  2. RootCauseAnalyzer — traces cascading failures to their source
  3. ImpactAssessor — predicts blast radius of failures
  4. CascadingFailurePredictor — anticipates downstream failures
  5. CorrelatedRecovery — coordinates recovery actions

This module is designed to be imported alongside the base Director:
    from director import Director, init_director
    from director_diagnostics import DiagnosticDirector, init_diagnostic_director

The DiagnosticDirector can be used as a drop-in replacement:
    director = init_diagnostic_director(supabase_client=client)
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import signal
import threading
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

# Import from base director
from director import (
    Director,
    Status,
    Health,
    Sector,
    Authority,
    Risk,
    Capability,
    Task,
    Observation,
    Decision,
    Goal,
    StrategicOutlook,
    ResourceAllocation,
    Briefing,
    SupabaseStore,
    InMemoryStore,
    CircuitBreaker,
    SupabaseErrorClassifier,
    Policy,
    FunctionObserver,
    sanitize_error_message,
    safe,
    iso,
    iso_now,
    now,
    utcnow,
    clamp,
    log,
)

# ═══════════════════════════════════════════════════════════════════════════
# SYSTEM TOPOLOGY MODEL
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class DependencyNode:
    """A node in the system dependency graph."""
    name: str
    sector: Sector
    depends_on: Set[str] = field(default_factory=set)
    depended_by: Set[str] = field(default_factory=set)
    criticality: int = 1  # 1=low, 5=critical
    last_health: Health = Health.UNKNOWN
    last_updated: datetime = field(default_factory=now)


class SystemTopology:
    """Dependency graph of all platform components.

    Models the relationships between services to understand:
      - Which services are upstream dependencies
      - Which services are downstream dependents
      - The blast radius of a failure
      - The root cause of cascading failures
    """

    def __init__(self):
        self.nodes: Dict[str, DependencyNode] = {}
        self._lock = threading.RLock()
        self._initialize_known_topology()

    def _initialize_known_topology(self) -> None:
        """Initialize the known dependency graph for Kanyoza platform."""
        # Phase 0: INFRASTRUCTURE (no dependencies)
        self.add_node("supabase", Sector.INFRA, criticality=5)
        self.add_node("redis", Sector.INFRA, criticality=5)
        self.add_node("event_bus", Sector.INFRA, criticality=5)

        # Phase 1: CORE SERVICES
        self.add_node("task_queue", Sector.INFRA, criticality=4)
        self.add_dependency("task_queue", "redis")
        self.add_dependency("task_queue", "supabase")

        self.add_node("job_service", Sector.INFRA, criticality=4)
        self.add_dependency("job_service", "supabase")
        self.add_dependency("job_service", "event_bus")

        self.add_node("plugin_registry", Sector.INFRA, criticality=3)
        self.add_dependency("plugin_registry", "event_bus")

        self.add_node("render_queue", Sector.CONTENT, criticality=3)
        self.add_dependency("render_queue", "redis")

        self.add_node("event_recorder", Sector.INFRA, criticality=2)
        self.add_dependency("event_recorder", "event_bus")
        self.add_dependency("event_recorder", "supabase")

        # Phase 2: BUSINESS SERVICES
        self.add_node("ai_orchestrator", Sector.HEALTH, criticality=4)
        self.add_dependency("ai_orchestrator", "supabase")

        self.add_node("content_engine", Sector.CONTENT, criticality=4)
        self.add_dependency("content_engine", "ai_orchestrator")
        self.add_dependency("content_engine", "event_bus")
        self.add_dependency("content_engine", "supabase")

        self.add_node("card_renderer", Sector.CONTENT, criticality=3)
        self.add_dependency("card_renderer", "render_queue")
        self.add_dependency("card_renderer", "content_engine")

        self.add_node("meta_engine", Sector.HEALTH, criticality=4)
        self.add_dependency("meta_engine", "event_bus")
        self.add_dependency("meta_engine", "supabase")

        self.add_node("condition_engine", Sector.LEARNING, criticality=4)
        self.add_dependency("condition_engine", "event_bus")
        self.add_dependency("condition_engine", "supabase")
        self.add_dependency("condition_engine", "content_engine")

        self.add_node("director", Sector.SELF, criticality=5)
        self.add_dependency("director", "event_bus")
        self.add_dependency("director", "supabase")

        self.add_node("brand_scheduler", Sector.CONTENT, criticality=3)
        self.add_dependency("brand_scheduler", "content_engine")
        self.add_dependency("brand_scheduler", "supabase")

        self.add_node("email_service", Sector.CLIENTS, criticality=3)
        self.add_dependency("email_service", "event_bus")

        self.add_node("engagement_tracker", Sector.GROWTH, criticality=3)
        self.add_dependency("engagement_tracker", "supabase")

        self.add_node("guardian", Sector.SECURITY, criticality=4)
        self.add_dependency("guardian", "supabase")

        self.add_node("guardian_scheduler", Sector.SECURITY, criticality=3)
        self.add_dependency("guardian_scheduler", "guardian")

        self.add_node("image_engine", Sector.CONTENT, criticality=3)
        self.add_dependency("image_engine", "render_queue")
        self.add_dependency("image_engine", "content_engine")

        self.add_node("topic_selector", Sector.CONTENT, criticality=2)
        self.add_dependency("topic_selector", "content_engine")

        self.add_node("prompt_builder", Sector.CONTENT, criticality=2)
        self.add_dependency("prompt_builder", "content_engine")

        # Phase 3: OBSERVABILITY
        self.add_node("health_monitor", Sector.HEALTH, criticality=3)
        self.add_dependency("health_monitor", "event_bus")

        self.add_node("enterprise_emitters", Sector.HEALTH, criticality=2)
        self.add_dependency("enterprise_emitters", "event_bus")

        # Plugin dependencies
        self.add_node("growthbridge_plugin", Sector.GROWTH, criticality=2)
        self.add_dependency("growthbridge_plugin", "content_engine")
        self.add_dependency("growthbridge_plugin", "supabase")

        self.add_node("monitor_plugin", Sector.HEALTH, criticality=2)
        self.add_dependency("monitor_plugin", "supabase")

        self.add_node("backup_plugin", Sector.INFRA, criticality=3)
        self.add_dependency("backup_plugin", "supabase")

        self.add_node("trend_plugin", Sector.GROWTH, criticality=2)
        self.add_dependency("trend_plugin", "supabase")

    def add_node(
        self, name: str, sector: Sector, criticality: int = 1
    ) -> None:
        """Add a node to the topology."""
        with self._lock:
            if name not in self.nodes:
                self.nodes[name] = DependencyNode(
                    name=name,
                    sector=sector,
                    criticality=criticality,
                )

    def add_dependency(
        self, dependent: str, dependency: str
    ) -> None:
        """Add a dependency: dependent depends on dependency."""
        with self._lock:
            self.add_node(dependent, Sector.INFRA)
            self.add_node(dependency, Sector.INFRA)
            self.nodes[dependent].depends_on.add(dependency)
            self.nodes[dependency].depended_by.add(dependent)

    def get_upstream(
        self, service: str, depth: int = 5
    ) -> Set[str]:
        """Get all upstream dependencies."""
        with self._lock:
            upstream = set()
            queue = deque([(service, 0)])
            visited = set()

            while queue:
                current, current_depth = queue.popleft()
                if current in visited or current_depth > depth:
                    continue
                visited.add(current)
                node = self.nodes.get(current)
                if node:
                    for dep in node.depends_on:
                        upstream.add(dep)
                        queue.append((dep, current_depth + 1))
            return upstream

    def get_downstream(
        self, service: str, depth: int = 5
    ) -> Set[str]:
        """Get all downstream dependents."""
        with self._lock:
            downstream = set()
            queue = deque([(service, 0)])
            visited = set()

            while queue:
                current, current_depth = queue.popleft()
                if current in visited or current_depth > depth:
                    continue
                visited.add(current)
                node = self.nodes.get(current)
                if node:
                    for dep in node.depended_by:
                        downstream.add(dep)
                        queue.append((dep, current_depth + 1))
            return downstream

    def get_blast_radius(self, service: str) -> int:
        """Calculate blast radius of a failure."""
        return len(self.get_downstream(service))

    def get_critical_downstream(
        self, service: str
    ) -> List[str]:
        """Get critical downstream services affected by a failure."""
        downstream = self.get_downstream(service)
        return [
            s for s in downstream
            if self.nodes.get(
                s, DependencyNode(s, Sector.INFRA)
            ).criticality >= 4
        ]

    def update_health(
        self, service: str, health: Health
    ) -> None:
        """Update service health in topology."""
        with self._lock:
            node = self.nodes.get(service)
            if node:
                node.last_health = health
                node.last_updated = now()

    def get_service_sector(self, service: str) -> Sector:
        """Get sector for a service."""
        with self._lock:
            node = self.nodes.get(service)
            return node.sector if node else Sector.INFRA

    def get_service_criticality(self, service: str) -> int:
        """Get criticality for a service."""
        with self._lock:
            node = self.nodes.get(service)
            return node.criticality if node else 1


# ═══════════════════════════════════════════════════════════════════════════
# ROOT CAUSE ANALYZER
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class RootCauseAnalysis:
    """Result of root cause analysis."""
    root_cause: str
    affected_services: List[str]
    confidence: float
    evidence: List[Dict[str, Any]]
    timestamp: datetime
    recommended_action: str
    analysis_id: str = field(
        default_factory=lambda: f"rca_{uuid.uuid4().hex[:12]}"
    )


class RootCauseAnalyzer:
    """Traces cascading failures to their source using topology."""

    def __init__(self, topology: SystemTopology):
        self.topology = topology
        self.analyses: deque = deque(maxlen=200)
        self._lock = threading.RLock()

    def analyze(
        self, observations: Dict[str, Observation]
    ) -> Optional[RootCauseAnalysis]:
        """Analyze observations to find root cause of failures."""
        with self._lock:
            failing_services = [
                name
                for name, obs in observations.items()
                if obs.status in (Health.CRITICAL, Health.OFFLINE)
            ]

            degraded_services = [
                name
                for name, obs in observations.items()
                if obs.status == Health.DEGRADED
            ]

            if not failing_services and not degraded_services:
                return None

            # Single failure — root cause is the service itself
            if len(failing_services) == 1 and not degraded_services:
                service = failing_services[0]
                return self._create_analysis(
                    root_cause=service,
                    affected=[service],
                    confidence=0.9,
                    evidence=[{
                        "type": "single_failure",
                        "service": service,
                    }],
                    action=f"Restart or repair {service}",
                )

            # Multiple failures — find common upstream
            all_failing = failing_services + degraded_services
            if len(all_failing) < 2:
                return None

            # Get upstream dependencies for each failing service
            upstream_sets = []
            for service in all_failing:
                upstream = self.topology.get_upstream(
                    service, depth=5
                )
                upstream_sets.append(upstream)

            # Find common upstream dependencies
            if upstream_sets:
                common_upstream = set.intersection(*upstream_sets)
            else:
                common_upstream = set()

            # Check if any common upstream is also failing
            failing_common = common_upstream & set(failing_services)

            if failing_common:
                # Root cause is a failing common upstream
                root_cause = self._select_root_cause(
                    failing_common
                )
                return self._create_analysis(
                    root_cause=root_cause,
                    affected=all_failing,
                    confidence=0.85,
                    evidence=[{
                        "type": "common_upstream_failure",
                        "upstream": root_cause,
                        "affected": all_failing,
                        "common_upstream": list(common_upstream),
                    }],
                    action=(
                        f"Fix root cause {root_cause} "
                        f"(affects {len(all_failing)} services)"
                    ),
                )

            # Check for infrastructure-level failure
            infra_deps = {"supabase", "redis", "event_bus"}
            failing_infra = infra_deps & set(failing_services)

            if failing_infra:
                root_cause = self._select_root_cause(
                    failing_infra
                )
                return self._create_analysis(
                    root_cause=root_cause,
                    affected=all_failing,
                    confidence=0.9,
                    evidence=[{
                        "type": "infrastructure_failure",
                        "infrastructure": root_cause,
                        "affected": all_failing,
                    }],
                    action=f"Restore infrastructure: {root_cause}",
                )

            # Check if common upstream is degraded (not critical)
            degraded_common = common_upstream & set(degraded_services)
            if degraded_common:
                root_cause = self._select_root_cause(
                    degraded_common
                )
                return self._create_analysis(
                    root_cause=root_cause,
                    affected=all_failing,
                    confidence=0.7,
                    evidence=[{
                        "type": "degraded_upstream",
                        "upstream": root_cause,
                        "affected": all_failing,
                    }],
                    action=(
                        f"Stabilize {root_cause} to prevent "
                        f"further cascading"
                    ),
                )

            # No clear root cause — use most critical service
            root_cause = max(
                all_failing,
                key=lambda s: self.topology.get_service_criticality(s),
            )
            return self._create_analysis(
                root_cause=root_cause,
                affected=all_failing,
                confidence=0.5,
                evidence=[{
                    "type": "unclear_causality",
                    "failing_services": all_failing,
                }],
                action=f"Investigate {root_cause} as likely root cause",
            )

    def _select_root_cause(
        self, candidates: Set[str]
    ) -> str:
        """Select the most likely root cause from candidates."""
        # Prefer infrastructure services
        infra = {"supabase", "redis", "event_bus"}
        infra_candidates = candidates & infra
        if infra_candidates:
            return min(
                infra_candidates,
                key=lambda s: self.topology.get_blast_radius(s),
            )

        # Otherwise, select by highest criticality and blast radius
        return max(
            candidates,
            key=lambda s: (
                self.topology.get_service_criticality(s),
                self.topology.get_blast_radius(s),
            ),
        )

    def _create_analysis(
        self,
        root_cause: str,
        affected: List[str],
        confidence: float,
        evidence: List[Dict[str, Any]],
        action: str,
    ) -> RootCauseAnalysis:
        """Create and store a root cause analysis."""
        analysis = RootCauseAnalysis(
            root_cause=root_cause,
            affected_services=affected,
            confidence=confidence,
            evidence=evidence,
            timestamp=now(),
            recommended_action=action,
        )
        self.analyses.append(analysis)
        return analysis

    def get_recent_analyses(
        self, limit: int = 10
    ) -> List[RootCauseAnalysis]:
        """Get recent root cause analyses."""
        return list(self.analyses)[-limit:]


# ═══════════════════════════════════════════════════════════════════════════
# IMPACT ASSESSOR
# ═══════════════════════════════════════════════════════════════════════════

class ImpactAssessor:
    """Assesses blast radius and severity of failures."""

    def __init__(self, topology: SystemTopology):
        self.topology = topology
        self.impact_history: deque = deque(maxlen=200)
        self._lock = threading.RLock()

    def assess_impact(
        self, service: str
    ) -> Dict[str, Any]:
        """Assess the impact of a service failure."""
        with self._lock:
            downstream = self.topology.get_downstream(
                service, depth=5
            )
            critical_downstream = (
                self.topology.get_critical_downstream(service)
            )

            severity = self._calculate_severity(
                len(downstream), len(critical_downstream)
            )

            impact = {
                "service": service,
                "blast_radius": len(downstream),
                "downstream_services": sorted(downstream),
                "critical_services_affected": sorted(
                    critical_downstream
                ),
                "severity": severity,
                "estimated_recovery_minutes": (
                    5 if severity == "low"
                    else 15 if severity == "medium"
                    else 30 if severity == "high"
                    else 60
                ),
                "timestamp": now(),
            }
            self.impact_history.append(impact)
            return impact

    def _calculate_severity(
        self,
        downstream_count: int,
        critical_count: int,
    ) -> str:
        """Calculate severity based on impact."""
        if critical_count >= 3 or downstream_count >= 10:
            return "critical"
        if critical_count >= 1 or downstream_count >= 5:
            return "high"
        if downstream_count >= 3:
            return "medium"
        return "low"

    def get_recent_impacts(
        self, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Get recent impact assessments."""
        return list(self.impact_history)[-limit:]


# ═══════════════════════════════════════════════════════════════════════════
# CASCADING FAILURE PREDICTOR
# ═══════════════════════════════════════════════════════════════════════════

class CascadingFailurePredictor:
    """Predicts which services will fail next during cascading failures."""

    def __init__(self, topology: SystemTopology):
        self.topology = topology
        self.predictions: deque = deque(maxlen=200)
        self._lock = threading.RLock()

    def predict_cascading_failures(
        self,
        failing_service: str,
        observations: Dict[str, Observation],
    ) -> List[Dict[str, Any]]:
        """Predict cascading failures from a failing service."""
        with self._lock:
            downstream = self.topology.get_downstream(
                failing_service, depth=3
            )
            predictions = []

            for service in downstream:
                obs = observations.get(service)
                if obs is None:
                    continue

                if obs.status == Health.HEALTHY:
                    predictions.append({
                        "service": service,
                        "current_status": "healthy",
                        "predicted_status": "degraded",
                        "time_to_failure_minutes": 5,
                        "confidence": 0.75,
                        "reason": (
                            f"Upstream dependency {failing_service} failed"
                        ),
                    })
                elif obs.status == Health.DEGRADED:
                    predictions.append({
                        "service": service,
                        "current_status": "degraded",
                        "predicted_status": "critical",
                        "time_to_failure_minutes": 10,
                        "confidence": 0.6,
                        "reason": (
                            f"Upstream {failing_service} failed "
                            f"and service already degraded"
                        ),
                    })
                elif obs.status == Health.UNKNOWN:
                    predictions.append({
                        "service": service,
                        "current_status": "unknown",
                        "predicted_status": "likely_degraded",
                        "time_to_failure_minutes": 15,
                        "confidence": 0.5,
                        "reason": (
                            f"Unknown health but depends on "
                            f"failed {failing_service}"
                        ),
                    })

            self.predictions.extend(predictions)
            return predictions

    def get_recent_predictions(
        self, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Get recent cascading failure predictions."""
        return list(self.predictions)[-limit:]


# ═══════════════════════════════════════════════════════════════════════════
# CORRELATED RECOVERY
# ═══════════════════════════════════════════════════════════════════════════

class CorrelatedRecovery:
    """Coordinates recovery actions for correlated failures."""

    def __init__(self):
        self.recovery_plans: deque = deque(maxlen=100)
        self._lock = threading.RLock()

    def create_recovery_plan(
        self, root_cause: str, affected_services: List[str]
    ) -> Dict[str, Any]:
        """Create a coordinated recovery plan."""
        with self._lock:
            plan = {
                "id": f"recovery_{uuid.uuid4().hex[:12]}",
                "root_cause": root_cause,
                "affected_services": affected_services,
                "steps": [
                    {
                        "order": 1,
                        "action": f"Restore {root_cause}",
                        "type": "root_cause_fix",
                        "status": "pending",
                    },
                    {
                        "order": 2,
                        "action": "Wait for root cause to stabilize",
                        "type": "stabilization",
                        "duration_seconds": 30,
                        "status": "pending",
                    },
                    {
                        "order": 3,
                        "action": (
                            "Verify downstream services recovered"
                        ),
                        "type": "verification",
                        "services": affected_services,
                        "status": "pending",
                    },
                    {
                        "order": 4,
                        "action": (
                            "Restart services that didn't recover"
                        ),
                        "type": "remediation",
                        "status": "pending",
                    },
                ],
                "created_at": now(),
                "status": "pending",
            }
            self.recovery_plans.append(plan)
            return plan

    def update_step_status(
        self, plan_id: str, step_order: int, status: str
    ) -> None:
        """Update the status of a recovery step."""
        with self._lock:
            for plan in self.recovery_plans:
                if plan["id"] == plan_id:
                    for step in plan["steps"]:
                        if step["order"] == step_order:
                            step["status"] = status
                            break
                    break

    def get_recovery_plans(
        self, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Get recent recovery plans."""
        return list(self.recovery_plans)[-limit:]


# ═══════════════════════════════════════════════════════════════════════════
# THE DIAGNOSTIC DIRECTOR
# ═══════════════════════════════════════════════════════════════════════════

class DiagnosticDirector(Director):
    """Director with full system diagnostic capabilities."""

    VERSION = "3.3.0"

    def __init__(
        self, supabase_client=None, interval: int = 60
    ):
        super().__init__(supabase_client, interval)

        # Diagnostic layer
        self.topology = SystemTopology()
        self.root_cause_analyzer = RootCauseAnalyzer(
            self.topology
        )
        self.impact_assessor = ImpactAssessor(self.topology)
        self.cascading_predictor = CascadingFailurePredictor(
            self.topology
        )
        self.recovery_coordinator = CorrelatedRecovery()

        # Track current state
        self.current_root_cause: Optional[RootCauseAnalysis] = None
        self.current_impact: Dict[str, Any] = {}
        self.current_predictions: List[Dict[str, Any]] = []

        log.info(
            "👔 KANYOZA DIRECTOR v%s — DIAGNOSTIC "
            "(topology + root cause + cascading prediction)",
            self.VERSION,
        )

    # ═══════════════════════════════════════════════════════════════════
    # ENHANCED OBSERVATION
    # ═══════════════════════════════════════════════════════════════════

    def observe_all(self) -> Dict[str, Observation]:
        """Observe with full diagnostic analysis."""
        observations = super().observe_all()

        # Update topology health
        for name, obs in observations.items():
            self.topology.update_health(name, obs.status)

        # Run root cause analysis
        root_cause = self.root_cause_analyzer.analyze(
            observations
        )
        if root_cause:
            self.current_root_cause = root_cause
            log.warning(
                "🔍 ROOT CAUSE: %s (affects %d services, "
                "confidence: %.0f%%)",
                root_cause.root_cause,
                len(root_cause.affected_services),
                root_cause.confidence * 100,
            )

            # Assess impact
            self.current_impact = self.impact_assessor.assess_impact(
                root_cause.root_cause
            )
            log.warning(
                "💥 IMPACT: %s (blast radius: %d, "
                "severity: %s)",
                root_cause.root_cause,
                self.current_impact.get("blast_radius", 0),
                self.current_impact.get("severity", "unknown"),
            )

            # Predict cascading failures
            self.current_predictions = (
                self.cascading_predictor.predict_cascading_failures(
                    root_cause.root_cause, observations
                )
            )
            for prediction in self.current_predictions[:5]:
                log.warning(
                    "🔮 CASCADING: %s → %s in %d min",
                    prediction["service"],
                    prediction["predicted_status"],
                    prediction["time_to_failure_minutes"],
                )

            # Create correlated recovery plan
            recovery_plan = (
                self.recovery_coordinator.create_recovery_plan(
                    root_cause.root_cause,
                    root_cause.affected_services,
                )
            )
            log.info(
                "📋 RECOVERY PLAN: %s", recovery_plan["id"]
            )

            # Create task to fix root cause
            self._create_task(
                Sector.HEALTH,
                f"Fix root cause: {root_cause.root_cause}",
                "restart_failed_service",
                (
                    f"Root cause: {root_cause.root_cause} "
                    f"affects {len(root_cause.affected_services)} services"
                ),
                0.95,
                {
                    "service_name": root_cause.root_cause,
                    "affected_services": root_cause.affected_services,
                    "recovery_plan_id": recovery_plan["id"],
                },
                root_cause.root_cause,
            )

            # Send diagnostic alert
            self._send_diagnostic_alert()

        return observations

    def _send_diagnostic_alert(self) -> None:
        """Send diagnostic alert via Telegram."""
        if not self.current_root_cause:
            return

        try:
            root_cause = self.current_root_cause
            impact = self.current_impact
            predictions = self.current_predictions

            parts = [
                f"🔍 <b>DIAGNOSTIC ALERT</b>",
                f"━━━━━━━━━━━━━━━━━━━",
                f"<b>Root Cause:</b> {root_cause.root_cause}",
                f"<b>Confidence:</b> {root_cause.confidence:.0%}",
                f"<b>Affected:</b> "
                f"{len(root_cause.affected_services)} services",
                f"<b>Blast Radius:</b> "
                f"{impact.get('blast_radius', 0)} services",
                f"<b>Severity:</b> "
                f"{impact.get('severity', 'unknown').upper()}",
            ]

            if predictions:
                parts.append("")
                parts.append("<b>Predicted Cascading:</b>")
                for p in predictions[:3]:
                    parts.append(
                        f"  • {p['service']} → "
                        f"{p['predicted_status']} "
                        f"in {p['time_to_failure_minutes']}min"
                    )

            parts.append("")
            parts.append(
                f"<b>Action:</b> {root_cause.recommended_action}"
            )
            parts.append(f"━━━━━━━━━━━━━━━━━━━")
            parts.append(
                f"<i>{now().strftime('%H:%M:%S UTC')}</i>"
            )

            self._send_telegram("\n".join(parts))
        except Exception as exc:
            log.error(
                "Diagnostic alert failed: %s",
                sanitize_error_message(exc),
            )

    # ═══════════════════════════════════════════════════════════════════
    # ENHANCED DECISION
    # ═══════════════════════════════════════════════════════════════════

    def decide(self) -> List[Task]:
        """Decide with diagnostic awareness."""
        base_decisions = super().decide()

        # Filter redundant tasks when root cause is known
        if self.current_root_cause:
            root_cause = self.current_root_cause.root_cause
            affected = set(
                self.current_root_cause.affected_services
            )

            filtered = []
            for task in base_decisions:
                if (
                    task.capability == "restart_failed_service"
                    and task.sector == Sector.HEALTH
                ):
                    service = task.params.get("service_name", "")
                    if (
                        service in affected
                        and service != root_cause
                    ):
                        continue
                filtered.append(task)
            base_decisions = filtered

            # Add verification task
            base_decisions.append(
                self._create_task(
                    Sector.HEALTH,
                    f"Verify recovery of {len(affected)} services",
                    "run_health_scan",
                    (
                        f"Verify services recovered "
                        f"after fixing {root_cause}"
                    ),
                    0.9,
                    {
                        "services": list(affected),
                        "root_cause": root_cause,
                    },
                    root_cause,
                )
            )

        return base_decisions

    # ═══════════════════════════════════════════════════════════════════
    # ENHANCED STATUS
    # ═══════════════════════════════════════════════════════════════════

    def status(self) -> dict:
        """Status with diagnostic metrics."""
        base_status = super().status()

        base_status.update({
            "diagnostics": {
                "version": self.VERSION,
                "topology": {
                    "nodes": len(self.topology.nodes),
                    "dependencies": sum(
                        len(n.depends_on)
                        for n in self.topology.nodes.values()
                    ),
                },
                "current_root_cause": self.get_root_cause(),
                "current_impact": self.current_impact,
                "cascading_predictions": (
                    self.current_predictions[:5]
                ),
                "recovery_plans": len(
                    self.recovery_coordinator.recovery_plans
                ),
            }
        })

        return base_status

    # ═══════════════════════════════════════════════════════════════════
    # DIAGNOSTIC METHODS
    # ═══════════════════════════════════════════════════════════════════

    def get_topology(self) -> Dict[str, Any]:
        """Get the system topology."""
        return {
            "nodes": {
                name: {
                    "sector": node.sector.value,
                    "depends_on": sorted(node.depends_on),
                    "depended_by": sorted(node.depended_by),
                    "criticality": node.criticality,
                    "last_health": node.last_health.value,
                    "last_updated": iso(node.last_updated),
                }
                for name, node in self.topology.nodes.items()
            }
        }

    def get_root_cause(self) -> Optional[Dict[str, Any]]:
        """Get current root cause analysis."""
        if self.current_root_cause:
            rca = self.current_root_cause
            return {
                "analysis_id": rca.analysis_id,
                "root_cause": rca.root_cause,
                "affected_services": rca.affected_services,
                "confidence": rca.confidence,
                "evidence": rca.evidence,
                "timestamp": iso(rca.timestamp),
                "recommended_action": rca.recommended_action,
            }
        return None

    def get_cascading_predictions(self) -> List[Dict[str, Any]]:
        """Get cascading failure predictions."""
        return [safe(p) for p in self.current_predictions]

    def get_impact_assessment(self) -> Dict[str, Any]:
        """Get current impact assessment."""
        return self.current_impact

    def get_recovery_plans(self) -> List[Dict[str, Any]]:
        """Get recovery plans."""
        return self.recovery_coordinator.get_recovery_plans()

    def get_recent_analyses(
        self, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Get recent root cause analyses."""
        analyses = self.root_cause_analyzer.get_recent_analyses(
            limit
        )
        return [
            {
                "analysis_id": a.analysis_id,
                "root_cause": a.root_cause,
                "affected_services": a.affected_services,
                "confidence": a.confidence,
                "timestamp": iso(a.timestamp),
                "recommended_action": a.recommended_action,
            }
            for a in analyses
        ]


# ═══════════════════════════════════════════════════════════════════════════
# FASTAPI APP FOR DIAGNOSTIC DIRECTOR
# ═══════════════════════════════════════════════════════════════════════════

def create_diagnostic_app(director: DiagnosticDirector):
    """Create FastAPI app with diagnostic endpoints."""
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
    from pydantic import BaseModel, Field

    app = FastAPI(
        title="Kanyoza Director — Diagnostic",
        version=director.VERSION,
    )

    security = HTTPBearer(auto_error=False)

    def _authenticate(
        credentials: Optional[HTTPAuthorizationCredentials],
        request: Request,
    ) -> bool:
        """Authenticate API requests."""
        master_token = os.getenv("MASTER_API_TOKEN", "")
        if not master_token:
            log.warning(
                "MASTER_API_TOKEN not set — "
                "Director API is UNAUTHENTICATED"
            )
            return False

        token = ""
        if credentials:
            token = credentials.credentials
        if not token:
            token = request.headers.get("X-API-Key", "")

        if not isinstance(token, str) or not isinstance(
            master_token, str
        ):
            return False
        return hmac.compare_digest(token, master_token)

    async def _require_auth(
        credentials: Optional[HTTPAuthorizationCredentials],
        request: Request,
    ) -> None:
        if not _authenticate(credentials, request):
            raise HTTPException(
                status_code=401,
                detail="Authentication required",
            )

    class ApprovalBody(BaseModel):
        actor: str = Field(
            default="admin",
            min_length=1,
            max_length=100,
            pattern=r"^[a-zA-Z0-9_\-\.\s]+$",
        )

    class RejectBody(BaseModel):
        actor: str = Field(
            default="admin",
            min_length=1,
            max_length=100,
            pattern=r"^[a-zA-Z0-9_\-\.\s]+$",
        )
        reason: str = Field(default="", max_length=500)

    # ── Health (no auth required) ──────────────────────────────────
    @app.get("/health")
    async def health_check():
        return {
            "status": "healthy" if director.running else "stopped",
            "version": director.VERSION,
            "uptime_hours": round(
                (now() - director.started).total_seconds() / 3600,
                2,
            ) if director.running else 0,
        }

    # ── Diagnostic endpoints (auth required) ───────────────────────
    @app.get("/diagnostics/topology")
    async def get_topology(
        credentials: Optional[HTTPAuthorizationCredentials] = None,
        request: Request = None,
    ):
        await _require_auth(credentials, request)
        return director.get_topology()

    @app.get("/diagnostics/root-cause")
    async def get_root_cause(
        credentials: Optional[HTTPAuthorizationCredentials] = None,
        request: Request = None,
    ):
        await _require_auth(credentials, request)
        result = director.get_root_cause()
        if result is None:
            return {"status": "no_root_cause", "data": None}
        return {"status": "root_cause_found", "data": result}

    @app.get("/diagnostics/cascading-predictions")
    async def get_predictions(
        credentials: Optional[HTTPAuthorizationCredentials] = None,
        request: Request = None,
    ):
        await _require_auth(credentials, request)
        return {
            "predictions": director.get_cascading_predictions()
        }

    @app.get("/diagnostics/impact")
    async def get_impact(
        credentials: Optional[HTTPAuthorizationCredentials] = None,
        request: Request = None,
    ):
        await _require_auth(credentials, request)
        return director.get_impact_assessment()

    @app.get("/diagnostics/recovery-plans")
    async def get_recovery_plans(
        credentials: Optional[HTTPAuthorizationCredentials] = None,
        request: Request = None,
    ):
        await _require_auth(credentials, request)
        return {
            "recovery_plans": director.get_recovery_plans()
        }

    @app.get("/diagnostics/recent-analyses")
    async def get_recent_analyses(
        limit: int = 10,
        credentials: Optional[HTTPAuthorizationCredentials] = None,
        request: Request = None,
    ):
        await _require_auth(credentials, request)
        return {
            "analyses": director.get_recent_analyses(limit)
        }

    # ── Standard endpoints (auth required) ─────────────────────────
    @app.get("/status")
    async def get_status(
        credentials: Optional[HTTPAuthorizationCredentials] = None,
        request: Request = None,
    ):
        await _require_auth(credentials, request)
        return director.status()

    @app.get("/briefing")
    async def get_briefing(
        credentials: Optional[HTTPAuthorizationCredentials] = None,
        request: Request = None,
    ):
        await _require_auth(credentials, request)
        return (
            director.briefings[-1]
            if director.briefings
            else director.briefing()
        )

    @app.get("/tasks")
    async def get_tasks(
        credentials: Optional[HTTPAuthorizationCredentials] = None,
        request: Request = None,
    ):
        await _require_auth(credentials, request)
        return [
            safe(asdict(t))
            for t in director.tasks.values()
        ]

    @app.get("/approvals")
    async def get_approvals(
        credentials: Optional[HTTPAuthorizationCredentials] = None,
        request: Request = None,
    ):
        await _require_auth(credentials, request)
        return [
            safe(asdict(t))
            for t in director.tasks.values()
            if t.status == Status.APPROVAL
        ]

    @app.post("/tasks/{task_id}/approve")
    async def approve_task(
        task_id: str,
        body: ApprovalBody,
        credentials: Optional[HTTPAuthorizationCredentials] = None,
        request: Request = None,
    ):
        await _require_auth(credentials, request)
        try:
            return safe(
                asdict(
                    director.approve(task_id, body.actor)
                )
            )
        except KeyError:
            raise HTTPException(404, "Task not found")
        except ValueError as e:
            raise HTTPException(409, str(e))

    @app.post("/tasks/{task_id}/reject")
    async def reject_task(
        task_id: str,
        body: RejectBody,
        credentials: Optional[HTTPAuthorizationCredentials] = None,
        request: Request = None,
    ):
        await _require_auth(credentials, request)
        try:
            return safe(
                asdict(
                    director.reject(
                        task_id, body.actor, body.reason
                    )
                )
            )
        except KeyError:
            raise HTTPException(404, "Task not found")

    @app.post("/observe")
    async def run_observe(
        credentials: Optional[HTTPAuthorizationCredentials] = None,
        request: Request = None,
    ):
        await _require_auth(credentials, request)
        director.observe_all()
        return director.status()

    @app.post("/plan")
    async def run_plan(
        credentials: Optional[HTTPAuthorizationCredentials] = None,
        request: Request = None,
    ):
        await _require_auth(credentials, request)
        return director.build_plan()

    return app


# ═══════════════════════════════════════════════════════════════════════════
# INITIALIZER
# ═══════════════════════════════════════════════════════════════════════════

diagnostic_director: Optional[DiagnosticDirector] = None


def init_diagnostic_director(
    supabase_client=None, auto_start: bool = True
) -> DiagnosticDirector:
    """Initialize the Diagnostic Director."""
    global diagnostic_director
    if diagnostic_director is None:
        diagnostic_director = DiagnosticDirector(
            supabase_client=supabase_client
        )
    if auto_start and not diagnostic_director.running:
        diagnostic_director.start()
    return diagnostic_director


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════

def main():
    """CLI entry point for Diagnostic Director."""
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(
        description="Kanyoza Diagnostic Director"
    )
    parser.add_argument(
        "--api",
        action="store_true",
        help="Run as API server",
    )
    parser.add_argument(
        "--host", default="0.0.0.0"
    )
    parser.add_argument(
        "--port", type=int, default=8091
    )
    args = parser.parse_args()

    d = init_diagnostic_director(auto_start=True)

    if args.api:
        app = create_diagnostic_app(d)

        def _signal_handler(signum, frame):
            log.info(
                "Received signal %d — shutting down",
                signum,
            )
            d.stop()
            raise SystemExit(0)

        signal.signal(signal.SIGTERM, _signal_handler)
        signal.signal(signal.SIGINT, _signal_handler)
        uvicorn.run(
            app, host=args.host, port=args.port
        )
    else:
        try:
            while d.running:
                time.sleep(5)
        except KeyboardInterrupt:
            d.stop()


if __name__ == "__main__":
    main()
