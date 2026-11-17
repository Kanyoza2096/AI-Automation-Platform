"""
KANYOZA DIRECTOR — Analysis Layer
==================================

System topology, root-cause diagnosis, impact assessment, cascading
failure prediction, correlated recovery, learning loop, adaptive
thresholds, function observers, and the engine registry.

v5.4 CHANGES:
  - record_outcome now recognizes VERIFIED, COMPLETED, INEFFECTIVE, and
    FAILED as the four signal-producing statuses. SKIPPED, APPROVAL,
    CANCELLED, PLANNED, EXECUTING, FLAGGED, and APPROVED no longer
    produce learning signals — those are decision outcomes, not
    execution attempts.
  - INEFFECTIVE is recorded as a failure with error_class =
    OUTCOME_NOT_VERIFIED. This distinguishes "handler threw an error"
    from "handler reported success but the world didn't change." The
    latter accumulates toward should_skip the same way any failure does.
  - record_outcome returns Optional[LearningSignal] — None when no
    signal is emitted (task didn't represent a real attempt).

v5.3.1 CHANGES (retained): replay works with dict and dataclass signals.
v5.3 CHANGES (retained): target-scoped, error-class aware, persistence-ready.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta
from statistics import mean
from typing import Any, Callable, Dict, List, Optional, Set

from .utils import now, clamp, sanitize_error_message
from .models import (
    Capability,
    DependencyNode,
    Health,
    LearningSignal,
    Observation,
    Risk,
    RootCauseAnalysis,
    Sector,
    Status,
    Task,
    Authority,
)

log = logging.getLogger("kanyoza.director")


# ═══════════════════════════════════════════════════════════════════════════
# POLICY
# ═══════════════════════════════════════════════════════════════════════════

class Policy:
    """Security policy for autonomous actions."""

    CRITICAL = {
        "delete_brand", "delete_client_data", "drop_database", "reset_database",
        "rotate_credentials", "change_production_secrets", "disable_security_controls",
        "change_billing", "refund_client", "send_legal_notice",
        "deploy_breaking_change", "change_access_control",
    }

    def authority(self, capability: Capability) -> Authority:
        if not capability.enabled:
            return Authority.ALERT
        if capability.name in self.CRITICAL or capability.risk == Risk.CRITICAL:
            return Authority.SUGGEST
        return capability.authority

    def requires_approval(self, capability: Capability) -> bool:
        return (
            capability.name in self.CRITICAL
            or capability.risk == Risk.CRITICAL
            or self.authority(capability) in (Authority.SUGGEST, Authority.ALERT)
        )


# ═══════════════════════════════════════════════════════════════════════════
# SYSTEM TOPOLOGY
# ═══════════════════════════════════════════════════════════════════════════

class SystemTopology:
    """Dependency graph of all platform components."""

    def __init__(self):
        self.nodes: Dict[str, DependencyNode] = {}
        self._initialize_known_topology()

    def _initialize_known_topology(self) -> None:
        """Project the canonical dependency graph with Director domain metadata.

        Dependency edges are never declared here. The canonical topology module
        is the only source of truth; this class adds only analysis metadata used
        by Director's impact and root-cause projections.
        """
        from core.engine_topology import CANONICAL_ENGINE_DEPENDENCIES

        metadata = {
            "supabase": (Sector.INFRA, 5),
            "redis": (Sector.INFRA, 5),
            "event_bus": (Sector.INFRA, 5),
            "task_queue": (Sector.INFRA, 4),
            "job_service": (Sector.INFRA, 4),
            "plugin_registry": (Sector.INFRA, 3),
            "render_queue": (Sector.CONTENT, 3),
            "event_recorder": (Sector.INFRA, 2),
            "ai_orchestrator": (Sector.HEALTH, 4),
            "content_engine": (Sector.CONTENT, 4),
            "card_renderer": (Sector.CONTENT, 3),
            "meta_engine": (Sector.HEALTH, 4),
            "condition_engine": (Sector.LEARNING, 4),
            "director": (Sector.SELF, 5),
            "brand_scheduler": (Sector.CONTENT, 3),
            "email_service": (Sector.CLIENTS, 3),
            "engagement_tracker": (Sector.GROWTH, 3),
            "guardian": (Sector.SECURITY, 4),
            "guardian_scheduler": (Sector.SECURITY, 3),
            "image_engine": (Sector.CONTENT, 3),
            "prompt_builder": (Sector.CONTENT, 2),
            "health_monitor": (Sector.HEALTH, 3),
            "enterprise_emitters": (Sector.HEALTH, 2),
            "gemini": (Sector.HEALTH, 3),
            "github": (Sector.SECURITY, 3),
            "job_stats_service": (Sector.INFRA, 2),
            "autonomous_content": (Sector.CONTENT, 3),
            "autonomous_loop": (Sector.SELF, 4),
        }

        for name in CANONICAL_ENGINE_DEPENDENCIES:
            sector, criticality = metadata.get(name, (Sector.INFRA, 2))
            self.add_node(name, sector, criticality=criticality)

        for dependent, dependencies in CANONICAL_ENGINE_DEPENDENCIES.items():
            for dependency in dependencies:
                self.add_dependency(dependent, dependency)

    def add_node(self, name: str, sector: Sector, criticality: int = 1) -> None:
        if name not in self.nodes:
            self.nodes[name] = DependencyNode(name=name, sector=sector, criticality=criticality)

    def add_dependency(self, dependent: str, dependency: str) -> None:
        self.add_node(dependent, Sector.INFRA)
        self.add_node(dependency, Sector.INFRA)
        self.nodes[dependent].depends_on.add(dependency)
        self.nodes[dependency].depended_by.add(dependent)

    def get_upstream(self, service: str, depth: int = 5) -> Set[str]:
        upstream = set()
        queue = deque([(service, 0)])
        visited = set()
        while queue:
            current, d = queue.popleft()
            if current in visited or d > depth:
                continue
            visited.add(current)
            node = self.nodes.get(current)
            if node:
                for dep in node.depends_on:
                    upstream.add(dep)
                    queue.append((dep, d + 1))
        return upstream

    def get_downstream(self, service: str, depth: int = 5) -> Set[str]:
        downstream = set()
        queue = deque([(service, 0)])
        visited = set()
        while queue:
            current, d = queue.popleft()
            if current in visited or d > depth:
                continue
            visited.add(current)
            node = self.nodes.get(current)
            if node:
                for dep in node.depended_by:
                    downstream.add(dep)
                    queue.append((dep, d + 1))
        return downstream

    def get_blast_radius(self, service: str) -> int:
        return len(self.get_downstream(service))

    def get_critical_downstream(self, service: str) -> List[str]:
        return [
            s for s in self.get_downstream(service)
            if self.nodes.get(s, DependencyNode(s, Sector.INFRA)).criticality >= 4
        ]

    def update_health(self, service: str, health: Health) -> None:
        node = self.nodes.get(service)
        if node:
            node.last_health = health
            node.last_updated = now()

    def get_service_sector(self, service: str) -> Sector:
        node = self.nodes.get(service)
        return node.sector if node else Sector.INFRA

    def get_service_criticality(self, service: str) -> int:
        node = self.nodes.get(service)
        return node.criticality if node else 1


# ═══════════════════════════════════════════════════════════════════════════
# ROOT CAUSE ANALYZER
# ═══════════════════════════════════════════════════════════════════════════

class RootCauseAnalyzer:
    """Traces cascading failures to their source."""

    def __init__(self, topology: SystemTopology):
        self.topology = topology
        self.analyses: deque = deque(maxlen=200)

    def analyze(self, observations: Dict[str, Observation]) -> Optional[RootCauseAnalysis]:
        failing = [n for n, o in observations.items() if o.status in (Health.CRITICAL, Health.OFFLINE)]
        degraded = [n for n, o in observations.items() if o.status == Health.DEGRADED]

        if not failing and not degraded:
            return None

        all_affected = failing + degraded

        if len(failing) == 1 and not degraded:
            service = failing[0]
            return self._create_analysis(service, [service], 0.9, f"Restart or repair {service}")

        if len(all_affected) < 2:
            return None

        upstream_sets = [self.topology.get_upstream(s) for s in all_affected]
        common = set.intersection(*upstream_sets) if upstream_sets else set()

        failing_common = common & set(failing)
        if failing_common:
            root = self._select_root_cause(failing_common)
            return self._create_analysis(root, all_affected, 0.85, f"Fix root cause {root}")

        infra = {"supabase", "redis", "event_bus"} & set(failing)
        if infra:
            root = self._select_root_cause(infra)
            return self._create_analysis(root, all_affected, 0.9, f"Restore infrastructure: {root}")

        degraded_common = common & set(degraded)
        if degraded_common:
            root = self._select_root_cause(degraded_common)
            return self._create_analysis(root, all_affected, 0.7, f"Stabilize {root}")

        root = max(all_affected, key=lambda s: self.topology.get_service_criticality(s))
        return self._create_analysis(root, all_affected, 0.5, f"Investigate {root}")

    def _select_root_cause(self, candidates: Set[str]) -> str:
        infra = {"supabase", "redis", "event_bus"} & candidates
        if infra:
            return min(infra, key=lambda s: self.topology.get_blast_radius(s))
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
        action: str,
    ) -> RootCauseAnalysis:
        analysis = RootCauseAnalysis(
            root_cause=root_cause,
            affected_services=affected,
            confidence=confidence,
            evidence=[{"type": "cascading_failure", "root": root_cause, "affected": affected}],
            timestamp=now(),
            recommended_action=action,
        )
        self.analyses.append(analysis)
        return analysis

    def get_recent_analyses(self, limit: int = 10) -> List[RootCauseAnalysis]:
        return list(self.analyses)[-limit:]


# ═══════════════════════════════════════════════════════════════════════════
# IMPACT ASSESSOR
# ═══════════════════════════════════════════════════════════════════════════

class ImpactAssessor:
    """Assesses blast radius and severity of failures."""

    def __init__(self, topology: SystemTopology):
        self.topology = topology
        self.impact_history: deque = deque(maxlen=200)

    def assess_impact(self, service: str) -> Dict[str, Any]:
        downstream = self.topology.get_downstream(service, depth=5)
        critical = self.topology.get_critical_downstream(service)
        severity = self._calculate_severity(len(downstream), len(critical))

        impact = {
            "service": service,
            "blast_radius": len(downstream),
            "downstream_services": sorted(downstream),
            "critical_services_affected": sorted(critical),
            "severity": severity,
            "estimated_recovery_minutes": {"low": 5, "medium": 15, "high": 30, "critical": 60}[severity],
            "timestamp": now(),
        }
        self.impact_history.append(impact)
        return impact

    def _calculate_severity(self, downstream_count: int, critical_count: int) -> str:
        if critical_count >= 3 or downstream_count >= 10:
            return "critical"
        if critical_count >= 1 or downstream_count >= 5:
            return "high"
        if downstream_count >= 3:
            return "medium"
        return "low"


# ═══════════════════════════════════════════════════════════════════════════
# CASCADING FAILURE PREDICTOR
# ═══════════════════════════════════════════════════════════════════════════

class CascadingFailurePredictor:
    """Predicts which services will fail next."""

    def __init__(self, topology: SystemTopology):
        self.topology = topology
        self.predictions: deque = deque(maxlen=200)

    def predict_cascading_failures(
        self,
        failing_service: str,
        observations: Dict[str, Observation],
    ) -> List[Dict[str, Any]]:
        downstream = self.topology.get_downstream(failing_service, depth=3)
        predictions = []

        for service in downstream:
            obs = observations.get(service)
            if obs is None:
                continue
            if obs.status == Health.NOMINAL:
                predictions.append({
                    "service": service, "current_status": "healthy",
                    "predicted_status": "degraded", "time_to_failure_minutes": 5,
                    "confidence": 0.75, "reason": f"Upstream {failing_service} failed",
                })
            elif obs.status == Health.DEGRADED:
                predictions.append({
                    "service": service, "current_status": "degraded",
                    "predicted_status": "critical", "time_to_failure_minutes": 10,
                    "confidence": 0.6, "reason": f"Upstream {failing_service} failed",
                })
            elif obs.status == Health.UNKNOWN:
                predictions.append({
                    "service": service, "current_status": "unknown",
                    "predicted_status": "likely_degraded", "time_to_failure_minutes": 15,
                    "confidence": 0.5, "reason": f"Depends on failed {failing_service}",
                })

        self.predictions.extend(predictions)
        return predictions


# ═══════════════════════════════════════════════════════════════════════════
# CORRELATED RECOVERY
# ═══════════════════════════════════════════════════════════════════════════

class CorrelatedRecovery:
    """Coordinates recovery actions for correlated failures."""

    def __init__(self):
        self.recovery_plans: deque = deque(maxlen=100)

    def create_recovery_plan(self, root_cause: str, affected_services: List[str]) -> Dict[str, Any]:
        plan = {
            "id": f"recovery_{uuid.uuid4().hex[:12]}",
            "root_cause": root_cause,
            "affected_services": affected_services,
            "steps": [
                {"order": 1, "action": f"Restore {root_cause}", "type": "root_cause_fix", "status": "pending"},
                {"order": 2, "action": "Wait for stabilization", "type": "stabilization", "duration_seconds": 30, "status": "pending"},
                {"order": 3, "action": "Verify downstream recovery", "type": "verification", "services": affected_services, "status": "pending"},
                {"order": 4, "action": "Restart unrecovered services", "type": "remediation", "status": "pending"},
            ],
            "created_at": now(),
            "status": "pending",
        }
        self.recovery_plans.append(plan)
        return plan

    def get_recovery_plans(self, limit: int = 10) -> List[Dict[str, Any]]:
        return list(self.recovery_plans)[-limit:]


# ═══════════════════════════════════════════════════════════════════════════
# LEARNING LOOP
# ═══════════════════════════════════════════════════════════════════════════

class LearningLoop:
    """
    Feeds task outcomes back into decision-making.

    v5.4:
      - VERIFIED, COMPLETED, INEFFECTIVE, and FAILED tasks produce signals.
      - VERIFIED is positive learning evidence; COMPLETED is explicitly
        unverified and is excluded from success/failure rates.
      - INEFFECTIVE is recorded as a failure with
        error_class=OUTCOME_NOT_VERIFIED. This distinguishes "handler
        threw an error" from "handler reported success but the world
        didn't change."

    v5.3.1: replay works with dict and dataclass signals.
    v5.3: target-scoped, error-class aware, persistence-ready.
    """

    PERMANENT_ERROR_CLASSES = frozenset({
        "VALIDATION_FAILED",
        "AUTH_REVOKED",
        "TARGET_NOT_FOUND",
        "PERMISSION_DENIED",
    })

    TRANSIENT_ERROR_CLASSES = frozenset({
        "NETWORK_TIMEOUT",
        "RATE_LIMITED",
        "CONNECTION_REFUSED",
        "SERVICE_UNAVAILABLE",
    })

    # Statuses that represent a real execution attempt (produce signals)
    # Only independently verified outcomes may influence adaptive learning.
    # Other terminal states remain available in task/decision diagnostics.
    _SIGNAL_STATUSES = frozenset({Status.VERIFIED})

    def __init__(self, max_signals: int = 1000, decay_days: int = 7):
        self.signals: deque = deque(maxlen=max_signals)
        self._replayed_signal_ids: Set[str] = set()
        self.capability_stats: Dict[str, Dict[str, float]] = {}
        self.failure_patterns: Dict[str, deque] = defaultdict(lambda: deque(maxlen=50))

        self.context_stats: Dict[str, Dict[str, int]] = defaultdict(
            lambda: {"successes": 0, "failures": 0, "unverified": 0}
        )
        self.time_stats: Dict[str, Dict[str, int]] = defaultdict(
            lambda: {"successes": 0, "failures": 0, "unverified": 0}
        )

        self.target_failure_counts: Dict[str, int] = defaultdict(int)
        self.target_last_error: Dict[str, str] = {}

        self.duration_stats: Dict[str, deque] = defaultdict(lambda: deque(maxlen=100))

        # v5.4: verification tracking
        self.verification_stats: Dict[str, Dict[str, int]] = defaultdict(
            lambda: {"verified": 0, "ineffective": 0, "unverified": 0}
        )

        self.decay_days = decay_days

    # ──────────────────────────────────────────────────────────────────
    # ERROR CLASSIFICATION
    # ──────────────────────────────────────────────────────────────────

    @staticmethod
    def classify_error(error: Optional[str]) -> str:
        if not error:
            return "UNKNOWN"
        head = error[:40]
        if ":" in head:
            prefix = head.split(":", 1)[0].strip()
            if prefix and prefix.replace("_", "").isalpha() and prefix.isupper():
                return prefix

        e = error.lower()
        if "timeout" in e or "timed out" in e:
            return "NETWORK_TIMEOUT"
        if "rate" in e and "limit" in e:
            return "RATE_LIMITED"
        if "connection refused" in e or "connection reset" in e:
            return "CONNECTION_REFUSED"
        if "unavailable" in e or "503" in e:
            return "SERVICE_UNAVAILABLE"
        if "auth" in e or "unauthorized" in e or "jwt" in e or "401" in e:
            return "AUTH_REVOKED"
        if "not found" in e or "404" in e:
            return "TARGET_NOT_FOUND"
        if "permission" in e or "forbidden" in e or "403" in e:
            return "PERMISSION_DENIED"
        if "validation" in e or "invalid" in e or "400" in e:
            return "VALIDATION_FAILED"
        return "UNKNOWN"

    # ──────────────────────────────────────────────────────────────────
    # TIME CONTEXT
    # ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _get_time_context(dt: Optional[datetime] = None) -> str:
        h = (dt or now()).hour
        if 0 <= h < 6:
            return "night"
        if 6 <= h < 12:
            return "morning"
        if 12 <= h < 18:
            return "afternoon"
        return "evening"

    @staticmethod
    def _get_day_context(dt: Optional[datetime] = None) -> str:
        d = (dt or now()).weekday()
        return "weekend" if d >= 5 else "weekday"

    def _context_key(self, sector: Sector, capability: str, target: Optional[str] = None) -> str:
        if target:
            return f"{sector.value}:{capability}:{target}"
        return f"{sector.value}:{capability}"

    def _time_key(self, sector: Sector, capability: str, target: Optional[str] = None) -> str:
        base = f"{sector.value}:{capability}"
        if target:
            base = f"{base}:{target}"
        return f"{base}:{self._get_time_context()}"

    @staticmethod
    def _target_key(capability: str, target: str) -> str:
        return f"{capability}:{target}"

    @staticmethod
    def _sig_get(s, key: str, default=None):
        if isinstance(s, dict):
            return s.get(key, default)
        return getattr(s, key, default)

    @staticmethod
    def _coerce_sector(value) -> Optional[Sector]:
        if isinstance(value, Sector):
            return value
        if isinstance(value, str):
            try:
                return Sector(value)
            except ValueError:
                return None
        return None

    # ──────────────────────────────────────────────────────────────────
    # RECORDING
    # ──────────────────────────────────────────────────────────────────

    def record_outcome(
        self,
        task: Task,
        duration_ms: float,
        target: Optional[str] = None,
    ) -> Optional[LearningSignal]:
        """Record a task outcome. Returns the emitted signal, or None if
        the task did not represent a real execution attempt.

        v5.4: distinguishes VERIFIED (predicate passed) from COMPLETED
        (handler reported success, no verification requested) from
        INEFFECTIVE (predicate failed) from FAILED (handler error).
        Only these four statuses produce signals.
        """
        # A task outcome is not a lesson unless its declared outcome predicate
        # passed and the task reached VERIFIED. Keep all other outcomes in the
        # task/decision ledger; do not train adaptive behavior from them.
        if task.status not in self._SIGNAL_STATUSES:
            return None

        if task.status == Status.VERIFIED:
            outcome = "success"
            error_class = None
        elif task.status == Status.COMPLETED:
            outcome = "unverified"
            error_class = None
        elif task.status == Status.INEFFECTIVE:
            outcome = "failure"
            error_class = "OUTCOME_NOT_VERIFIED"
        else:  # FAILED
            outcome = "failure"
            error_class = self.classify_error(task.error)

        # Verification stats
        if task.status == Status.VERIFIED:
            self.verification_stats[task.capability]["verified"] += 1
        elif task.status == Status.INEFFECTIVE:
            self.verification_stats[task.capability]["ineffective"] += 1
        elif task.status == Status.COMPLETED:
            self.verification_stats[task.capability]["unverified"] += 1

        signal = LearningSignal(
            capability=task.capability,
            sector=task.sector,
            outcome=outcome,
            duration_ms=duration_ms,
            error_type=task.error[:100] if task.error else None,
            context={
                "authority": task.authority.value,
                "risk": task.risk.value,
                "confidence": task.confidence,
                "time_context": self._get_time_context(),
                "day_context": self._get_day_context(),
                "target": target,
                "error_class": error_class,
                "status": task.status.value,
            },
            timestamp=now(),
        )
        self.signals.append(signal)

        stats = self.capability_stats.setdefault(
            task.capability,
            {"successes": 0, "failures": 0, "unverified": 0, "total_duration_ms": 0},
        )
        stats.setdefault("unverified", 0)
        stats["successes" if outcome == "success" else "failures" if outcome == "failure" else "unverified"] += 1
        stats["total_duration_ms"] += duration_ms

        ctx_key = self._context_key(task.sector, task.capability, target)
        self.context_stats[ctx_key][
            "successes" if outcome == "success" else "failures" if outcome == "failure" else "unverified"
        ] += 1

        time_key = self._time_key(task.sector, task.capability, target)
        self.time_stats[time_key][
            "successes" if outcome == "success" else "failures" if outcome == "failure" else "unverified"
        ] += 1

        self.duration_stats[task.capability].append(duration_ms)

        if outcome == "unverified":
            return signal

        if outcome == "failure":
            self.failure_patterns[task.capability].append({
                "timestamp": signal.timestamp,
                "error": signal.error_type,
                "error_class": error_class,
                "context": ctx_key,
                "target": target,
            })
            if target:
                tkey = self._target_key(task.capability, target)
                self.target_failure_counts[tkey] += 1
                self.target_last_error[tkey] = error_class or "UNKNOWN"
            return signal

        if target:
            tkey = self._target_key(task.capability, target)
            self.target_failure_counts.pop(tkey, None)
            self.target_last_error.pop(tkey, None)

            patterns = self.failure_patterns.get(task.capability)
            if patterns:
                self.failure_patterns[task.capability] = deque(
                    [p for p in patterns if p.get("target") != target],
                    maxlen=patterns.maxlen or 50,
                )

            self.context_stats[ctx_key] = {"successes": 0, "failures": 0, "unverified": 0}
            self.time_stats[time_key] = {"successes": 0, "failures": 0, "unverified": 0}

        return signal

    # ──────────────────────────────────────────────────────────────────
    # QUERY METHODS
    # ──────────────────────────────────────────────────────────────────

    def get_success_rate(self, capability: str) -> Optional[float]:
        stats = self.capability_stats.get(capability)
        if not stats:
            return None
        total = stats["successes"] + stats["failures"]
        return stats["successes"] / total if total > 0 else None

    def get_recent_success_rate(self, capability: str, days: Optional[int] = None) -> Optional[float]:
        days = days or self.decay_days
        cutoff = now() - timedelta(days=days)
        recent_window = [
            s for s in self.signals
            if s.capability == capability and s.timestamp >= cutoff
        ]
        recent_verified = [s for s in recent_window if s.outcome in ("success", "failure")]
        if not recent_verified:
            # Recent unverified attempts make recent reliability unknown; do not
            # silently fall back to older verified samples as if they were recent.
            return None if recent_window else self.get_success_rate(capability)
        successes = sum(1 for s in recent_verified if s.outcome == "success")
        return successes / len(recent_verified)

    def get_context_success_rate(
        self,
        sector: Sector,
        capability: str,
        target: Optional[str] = None,
    ) -> Optional[float]:
        ctx_key = self._context_key(sector, capability, target)
        stats = self.context_stats.get(ctx_key)
        if not stats:
            return None
        total = stats["successes"] + stats["failures"]
        if total < 3:
            return None
        return stats["successes"] / total

    def get_time_success_rate(
        self,
        sector: Sector,
        capability: str,
        time_context: Optional[str] = None,
        target: Optional[str] = None,
    ) -> Optional[float]:
        time_context = time_context or self._get_time_context()
        base = f"{sector.value}:{capability}"
        if target:
            base = f"{base}:{target}"
        time_key = f"{base}:{time_context}"
        stats = self.time_stats.get(time_key)
        if not stats:
            return None
        total = stats["successes"] + stats["failures"]
        if total < 3:
            return None
        return stats["successes"] / total

    def get_avg_duration_ms(self, capability: str) -> Optional[float]:
        durations = self.duration_stats.get(capability)
        if not durations:
            return None
        return mean(durations)

    def get_recurring_failures(
        self,
        capability: str,
        threshold: int = 3,
        target: Optional[str] = None,
    ) -> bool:
        if target:
            tkey = self._target_key(capability, target)
            return self.target_failure_counts.get(tkey, 0) >= threshold
        return len(self.failure_patterns.get(capability, [])) >= threshold

    def get_verification_rate(self, capability: str) -> Optional[float]:
        """Fraction of verified outcomes among verified + ineffective.
        
        Returns None if the capability hasn't had any verification-
        requested attempts. A value of 1.0 means every verified attempt
        succeeded. A value of 0.0 means every verified attempt was
        ineffective.
        """
        stats = self.verification_stats.get(capability)
        if not stats:
            return None
        total = stats["verified"] + stats["ineffective"]
        if total == 0:
            return None
        return stats["verified"] / total

    # ──────────────────────────────────────────────────────────────────
    # DECISION HELPERS
    # ──────────────────────────────────────────────────────────────────

    def should_escalate(
        self,
        sector: Sector,
        capability: str,
        target: Optional[str] = None,
    ) -> bool:
        ctx_rate = self.get_context_success_rate(sector, capability, target)
        if ctx_rate is not None and ctx_rate < 0.5:
            return True

        time_rate = self.get_time_success_rate(sector, capability, target=target)
        if time_rate is not None and time_rate < 0.4:
            return True

        return False

    def should_skip(
        self,
        sector: Sector,
        capability: str,
        target: Optional[str] = None,
    ) -> bool:
        if target:
            tkey = self._target_key(capability, target)
            last_err = self.target_last_error.get(tkey)
            if last_err in self.PERMANENT_ERROR_CLASSES:
                return True

        ctx_rate = self.get_context_success_rate(sector, capability, target)
        if ctx_rate is not None and ctx_rate < 0.2:
            return True

        time_rate = self.get_time_success_rate(sector, capability, target=target)
        if time_rate is not None and time_rate < 0.15:
            return True

        return False

    def adjust_confidence(self, task: Task, target: Optional[str] = None) -> float:
        weights = []
        rates = []

        global_rate = self.get_success_rate(task.capability)
        if global_rate is not None:
            rates.append(global_rate)
            weights.append(0.30)

        recent_rate = self.get_recent_success_rate(task.capability)
        if recent_rate is not None:
            rates.append(recent_rate)
            weights.append(0.40)

        ctx_rate = self.get_context_success_rate(task.sector, task.capability, target)
        if ctx_rate is not None:
            rates.append(ctx_rate)
            weights.append(0.30)

        if not rates:
            return task.confidence

        total_weight = sum(weights)
        weighted_rate = sum(r * w for r, w in zip(rates, weights)) / total_weight
        return clamp(task.confidence * 0.3 + weighted_rate * 0.7, 0.1, 0.99)

    # ──────────────────────────────────────────────────────────────────
    # PERSISTENCE / REPLAY
    # ──────────────────────────────────────────────────────────────────

    def rebuild_from_signals(self, signals: list) -> int:
        replayed = 0
        for s in signals:
            try:
                signal_id = self._sig_get(s, "id")
                if not signal_id or str(signal_id) in self._replayed_signal_ids:
                    continue
                if self._replay_signal(s):
                    self._replayed_signal_ids.add(str(signal_id))
                    replayed += 1
            except Exception as exc:
                log.warning("rebuild_from_signals: skipping malformed signal — %s", exc)
        return replayed

    def _replay_signal(self, s) -> bool:
        capability = self._sig_get(s, "capability")
        sector = self._coerce_sector(self._sig_get(s, "sector"))
        outcome = self._sig_get(s, "outcome")

        if not capability or sector is None or outcome != "success":
            return False

        duration_ms = float(self._sig_get(s, "duration_ms", 0) or 0)
        error_type = self._sig_get(s, "error_type")
        timestamp = self._sig_get(s, "timestamp") or now()
        if isinstance(timestamp, str):
            try:
                timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except Exception:
                timestamp = now()

        context = self._sig_get(s, "context") or {}
        if not isinstance(context, dict):
            return False
        # Fail closed for legacy/unproven records: a verified status and a
        # durable row identity are both required before replay.
        if not self._sig_get(s, "id") or context.get("status") != Status.VERIFIED.value:
            return False
        target = context.get("target")
        error_class = context.get("error_class") or (
            self.classify_error(error_type) if outcome == "failure" else None
        )
        task_status_str = context.get("status")
        # Historical COMPLETED records used "success" before verification
        # was separated. Reclassify them conservatively during replay.
        if task_status_str == Status.COMPLETED.value and outcome == "success":
            outcome = "unverified"

        # Verification stats during replay
        if task_status_str == Status.VERIFIED.value:
            self.verification_stats[capability]["verified"] += 1
        elif task_status_str == Status.INEFFECTIVE.value:
            self.verification_stats[capability]["ineffective"] += 1
        elif task_status_str == Status.COMPLETED.value:
            self.verification_stats[capability]["unverified"] += 1

        stats = self.capability_stats.setdefault(
            capability,
            {"successes": 0, "failures": 0, "unverified": 0, "total_duration_ms": 0},
        )
        stats.setdefault("unverified", 0)
        stats["successes" if outcome == "success" else "failures" if outcome == "failure" else "unverified"] += 1
        stats["total_duration_ms"] += duration_ms

        ctx_key = self._context_key(sector, capability, target)
        self.context_stats[ctx_key][
            "successes" if outcome == "success" else "failures" if outcome == "failure" else "unverified"
        ] += 1

        time_bucket = self._get_time_context(timestamp)
        base = f"{sector.value}:{capability}"
        if target:
            base = f"{base}:{target}"
        time_key = f"{base}:{time_bucket}"
        self.time_stats[time_key][
            "successes" if outcome == "success" else "failures" if outcome == "failure" else "unverified"
        ] += 1

        self.duration_stats[capability].append(duration_ms)

        if outcome == "unverified":
            return True

        if outcome == "failure":
            self.failure_patterns[capability].append({
                "timestamp": timestamp,
                "error": error_type,
                "error_class": error_class,
                "context": ctx_key,
                "target": target,
            })
            if target:
                tkey = self._target_key(capability, target)
                self.target_failure_counts[tkey] += 1
                self.target_last_error[tkey] = error_class or "UNKNOWN"
        elif outcome == "success":
            if target:
                tkey = self._target_key(capability, target)
                self.target_failure_counts.pop(tkey, None)
                self.target_last_error.pop(tkey, None)

        return True

    # ──────────────────────────────────────────────────────────────────
    # REPORTING
    # ──────────────────────────────────────────────────────────────────

    def get_learning_report(self) -> Dict[str, Any]:
        return {
            "total_signals": len(self.signals),
            "capabilities_tracked": len(self.capability_stats),
            "contexts_tracked": len(self.context_stats),
            "time_buckets_tracked": len(self.time_stats),
            "targets_tracked": len(self.target_failure_counts),
            "capabilities": {
                cap: {
                    "success_rate": (
                        round(self.get_success_rate(cap), 2)
                        if self.get_success_rate(cap) is not None else None
                    ),
                    "recent_rate": (
                        round(self.get_recent_success_rate(cap), 2)
                        if self.get_recent_success_rate(cap) is not None else None
                    ),
                    "avg_duration_ms": round(self.get_avg_duration_ms(cap) or 0, 0),
                    "total": stats["successes"] + stats["failures"] + stats.get("unverified", 0),
                    "verified_outcome_count": stats["successes"] + stats["failures"],
                    "unverified_outcome_count": stats.get("unverified", 0),
                    "verification_rate": (
                        round(self.get_verification_rate(cap) or 0, 2)
                        if self.get_verification_rate(cap) is not None else None
                    ),
                    "verification_counts": dict(self.verification_stats.get(cap, {})),
                }
                for cap, stats in self.capability_stats.items()
            },
        }


# ═══════════════════════════════════════════════════════════════════════════
# ADAPTIVE THRESHOLDS
# ═══════════════════════════════════════════════════════════════════════════

class AdaptiveThresholds:
    """Self-tuning decision thresholds."""

    def __init__(self):
        self.thresholds: Dict[str, float] = {
            "health_scan": 90.0,
            "security_scan": 80.0,
            "brand_revival": 60.0,
            "content_optimization": 70.0,
        }

    def get_threshold(self, name: str) -> float:
        return self.thresholds.get(name, 80.0)

    def update_threshold(self, name: str, actual_value: float, outcome: str) -> None:
        current = self.thresholds.get(name, 80.0)
        if outcome == "false_positive":
            self.thresholds[name] = clamp(current - 5, lo=50.0, hi=95.0)
        elif outcome == "true_positive":
            self.thresholds[name] = clamp(current + 2, lo=50.0, hi=95.0)
        elif outcome == "missed":
            self.thresholds[name] = clamp(current + 5, lo=50.0, hi=95.0)


# ═══════════════════════════════════════════════════════════════════════════
# FUNCTION OBSERVER
# ═══════════════════════════════════════════════════════════════════════════

class FunctionObserver:
    def __init__(self, name: str, sector: Sector, fn: Callable):
        self.name = name
        self.sector = sector
        self.fn = fn

    def observe(self) -> Observation:
        try:
            result = self.fn() or {}
        except Exception as exc:
            # Probe failure is missing evidence, not proof that the engine is critical.
            return Observation(
                self.name, self.sector, Health.UNKNOWN,
                issues=[{
                    "type": "observer_failure",
                    "error": sanitize_error_message(exc),
                    "evidence_confidence": "low",
                }],
                health_contract="probe_failure",
            )

        if isinstance(result, Observation):
            return result
        if not isinstance(result, dict):
            result = {}

        status_str = str(result.get("status", "unknown")).strip().lower()
        status_map = {
            "ok": Health.NOMINAL,
            "healthy": Health.NOMINAL,
            "nominal": Health.NOMINAL,
            "ready": Health.NOMINAL,
            "up": Health.NOMINAL,
            "degraded": Health.DEGRADED,
            "warning": Health.DEGRADED,
            "critical": Health.CRITICAL,
            "failed": Health.CRITICAL,
            "offline": Health.OFFLINE,
            "down": Health.OFFLINE,
        }
        observed_at = result.get("observed_at") or result.get("timestamp")
        timestamp = now()
        timestamp_valid = True
        if observed_at is not None:
            try:
                timestamp = (
                    observed_at if isinstance(observed_at, datetime)
                    else datetime.fromisoformat(str(observed_at).replace("Z", "+00:00"))
                )
            except (TypeError, ValueError):
                timestamp_valid = False

        return Observation(
            self.name,
            self.sector,
            status_map.get(status_str, Health.UNKNOWN),
            result.get("metrics", {}),
            result.get("issues", []),
            result.get("message", ""),
            timestamp=timestamp,
            health_contract=str(result.get("health_contract") or "explicit_status"),
            contract_version=str(result.get("contract_version") or "kdo.observer.v1"),
            timestamp_valid=timestamp_valid,
        )


# ═══════════════════════════════════════════════════════════════════════════
# ENGINE AUTO-DISCOVERY REGISTRY
# ═══════════════════════════════════════════════════════════════════════════

ENGINE_REGISTRY = [
    ("services.content_engine", "content_engine", "content_engine", Sector.CONTENT),
    ("services.card_renderer", "card_renderer", "card_renderer", Sector.CONTENT),
    ("services.condition_engine", "autonomous_engine", "condition_engine", Sector.LEARNING),
    ("services.email_service", "email_service", "email_service", Sector.CLIENTS),
    ("services.engagement_tracker", "engagement_tracker", "engagement_tracker", Sector.GROWTH),
    ("services.guardian_service", "guardian_service", "guardian", Sector.SECURITY),
    ("services.meta_engine", "meta_engine", "meta_engine", Sector.HEALTH),
    ("services.director", "director", "meta_director", Sector.SELF),
    ("ai.orchestrator", "AIOrchestrator", "ai_orchestrator", Sector.HEALTH),
    ("scheduler.brand_scheduler", "BrandSchedulerManager", "brand_scheduler", Sector.CONTENT),
    ("services.job_stats_service", "get_job_stats_service", "job_stats_service", Sector.INFRA),
    ("plugins.base", "PluginRegistry", "plugin_registry", Sector.INFRA),
    ("core.event_bus", "EventBus", "event_bus", Sector.INFRA),
    ("core.render_queue", "get_render_queue", "render_queue", Sector.CONTENT),
    ("services.image_engine", "generate_best_image", "image_engine", Sector.CONTENT),
    ("services.prompt_builder", "prompt_builder", "prompt_builder", Sector.CONTENT),
    ("services.health_monitor_service", "HealthMonitorService", "health_monitor", Sector.HEALTH),
]


__all__ = [
    "Policy",
    "SystemTopology",
    "RootCauseAnalyzer",
    "ImpactAssessor",
    "CascadingFailurePredictor",
    "CorrelatedRecovery",
    "LearningLoop",
    "AdaptiveThresholds",
    "FunctionObserver",
    "ENGINE_REGISTRY",
]
