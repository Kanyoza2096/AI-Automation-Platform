"""
KANYOZA AUTONOMOUS CONDITION + DECISION ENGINE v5.1
====================================================

COMPLETE ASYNC-NATIVE REWRITE + ENHANCED LEARNING

Version: 5.1.0
Purpose:
    Complete autonomous decision intelligence for social-media/content systems.

v5.1 CHANGES (Additive — nothing removed):
    - ADDED: Context-aware learning (topic + content_type + style tracking)
    - ADDED: Time-of-day pattern learning (when content works best)
    - ADDED: Causal chain reasoning (why decisions succeeded/failed)
    - ADDED: Recency-weighted strategy scoring (7-day decay)
    - ADDED: Automatic strategy escalation for unreliable contexts
    - ADDED: Bayesian success tracking (per-topic priors)
    - ADDED: Duration-aware strategy ranking
    - ADDED: 7 new audit endpoints for learning introspection
    - All v5.0 features preserved

v5.0 CHANGES (Additive — nothing removed):
    - Async-native health check integration
    - Bounded collections (deque everywhere)
    - Correlation ID tracking
    - Enhanced thread safety
    - Service name property for health registry
    - Clean code organization

RETAINED FROM V4.0.1:
    - Multi-metric baselines
    - Per-topic baselines
    - Predictive trend analysis
    - Anomaly classification (SPIKE, DIP, DRIFT, LEVEL_SHIFT)
    - Root cause hypotheses
    - Brand clustering
    - Adaptive thresholds
    - Confidence calibration
    - Automated experiments with auto-deployment
    - UCB strategy selection
    - Community Council integration
    - Event bus integration
    - Supabase persistence
    - Goal tracking
    - Decision memory
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import random
import statistics
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Callable, Deque, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger("kanyoza.condition_engine")


# ═══════════════════════════════════════════════════════════════════════════
# COUNCIL INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════

try:
    from council.integration import get_council
    HAS_COUNCIL = True
except ImportError:
    HAS_COUNCIL = False
    logger.debug("Council not available — continuing without community oversight")


# ═══════════════════════════════════════════════════════════════════════════
# EVENT BUS INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════


try:
    from core.unified_bus import get_unified_bus
    HAS_EVENT_BUS = True
except ImportError:
    HAS_EVENT_BUS = False
    logger.warning("UnifiedEventBus not available — events will be skipped")


async def _publish_event_async(event_name: str, payload: Dict[str, Any]) -> None:
    """Async publish on UnifiedEventBus."""
    if not HAS_EVENT_BUS:
        return
    pl = dict(payload or {})
    pl.setdefault("workspace_id", "platform")
    try:
        bus = await get_unified_bus()
        try:
            await bus.publish(event_name, pl, source="condition_engine", workspace_id="platform")
        except TypeError:
            await bus.publish(event_name, pl)
    except Exception as exc:
        logger.debug("Condition Engine publish failed for %s: %s", event_name, exc)


# ═══════════════════════════════════════════════════════════════════════════
# PACKAGE IMPORTS (explicit — leaf-first, no wildcards, no cycles)
# ═══════════════════════════════════════════════════════════════════════════

from .models import (
    ActionPlan,
    ActionRisk,
    ActionStatus,
    Anomaly,
    AnomalyType,
    Baseline,
    Decision,
    DecisionMemory,
    DecisionState,
    EngineConfig,
    Experiment,
    Goal,
    GoalStatus,
    Observation,
    Strategy,
    Trend,
)
from .analysis import (
    AdaptiveThresholdManager,
    AnomalyClassifier,
    BrandClusterer,
    ContextLearner,
    PredictiveTrendAnalyzer,
)
from .utils import (
    _publish_event,
    clamp,
    day_context,
    generate_id,
    iso_now,
    parse_datetime,
    safe_mean,
    safe_median,
    safe_stddev,
    time_bucket,
    utc_now,
)


class AutonomousConditionEngine:
    """Complete autonomous condition + decision engine — v5.1 Enhanced Learning."""
    
    VERSION = "5.1.0"
    
    def __init__(
        self,
        supabase_client: Any = None,
        config: Optional[EngineConfig] = None,
        action_executor: Optional[Callable[[ActionPlan], Dict[str, Any]]] = None,
        event_bus: Any = None,
        platform_state_reader: Any = None,
        **kwargs: Any,
    ):
        self.supabase = supabase_client
        self.config = config or EngineConfig()
        self.action_executor = action_executor
        self._event_bus = event_bus
        self._platform_state_reader = platform_state_reader
        if self._platform_state_reader is None and supabase_client is not None:
            try:
                from core.platform_state_reader import PlatformStateReader
                self._platform_state_reader = PlatformStateReader(supabase_client)
            except Exception:
                logger.warning("ConditionEngine Platform State reader unavailable; shared state remains unknown")
        self._platform_state_context: Dict[str, Any] = {
            "status": "unavailable", "freshness": "unknown", "revision": 0,
            "services": [], "capabilities": [], "observer_observations": [],
            "reason": "platform_state_reader_not_configured",
        }
        if kwargs:
            logger.debug("ConditionEngine ignoring extra kwargs: %s", list(kwargs.keys()))
        self._lock = threading.RLock()
        
        # Bounded collections
        self._observations: Dict[str, Deque[Observation]] = defaultdict(
            lambda: deque(maxlen=self.config.max_observations_per_brand)
        )
        self._baselines: Dict[Tuple[str, str], Baseline] = {}
        self._trends: Dict[Tuple[str, str], Trend] = {}
        self._brand_health: Dict[str, Dict[str, Any]] = {}
        self._decisions: Dict[str, Decision] = {}
        self._plans: Dict[str, ActionPlan] = {}
        self._decision_memory: Deque[DecisionMemory] = deque(maxlen=self.config.max_decision_memory)
        self._goals: Dict[str, Goal] = {}
        self._strategies: Dict[str, Strategy] = {}
        self._experiments: Dict[str, Experiment] = {}
        self._last_action_at: Dict[Tuple[str, str], datetime] = {}
        self._states: Dict[str, DecisionState] = {}
        
        # Analyzers
        self.anomaly_classifier = AnomalyClassifier()
        self.trend_analyzer = PredictiveTrendAnalyzer()
        self.brand_clusterer = BrandClusterer()
        self.threshold_manager = AdaptiveThresholdManager(config)
        self._topic_baselines: Dict[Tuple[str, str, str], Baseline] = {}
        self._confidence_history: Dict[str, deque] = defaultdict(lambda: deque(maxlen=100))
        
        # v5.1 — Context learning
        self.context_learner = ContextLearner(self.config)
        
        # Council
        self.council = get_council() if HAS_COUNCIL else None
        if self.council:
            logger.info("ConditionEngine v%s — Council activated (%d members)", self.VERSION, len(self.council.members))
        
        self._register_default_strategies()
        logger.info("Autonomous Condition Engine v%s — Enhanced Learning Edition", self.VERSION)
    
    # ═══════════════════════════════════════════════════════════════════════
    # LIFECYCLE
    # ═══════════════════════════════════════════════════════════════════════
    
    @property
    def service_name(self) -> str:
        return "condition_engine"
    
    def set_event_bus(self, event_bus: Any) -> None:
        """Wire event bus after construction."""
        self._event_bus = event_bus
    
    def readiness_report(self) -> Dict[str, Any]:
        """Report analysis, action-execution, and persistence readiness separately.

        A callable fallback that deliberately refuses execution is not an action
        executor. A configured persistence client is not proof that durable writes
        succeed, so persistence remains unknown until a safe durability probe exists.
        """
        executor = self.action_executor
        executor_name = getattr(executor, "__name__", "") if executor is not None else ""
        unavailable_executor = bool(getattr(executor, "condition_engine_unavailable", False)) or executor_name == "default_action_executor"
        executor_callable = callable(executor) and not unavailable_executor
        persistence_configured = self.supabase is not None
        return {
            "analysis_ready": True,
            "execution_ready": executor_callable,
            "persistence_ready": False,
            "persistence_status": "unknown" if persistence_configured else "unavailable",
            "executor_status": "bound" if executor_callable else "unavailable",
            "executor_binding": getattr(self, "_executor_binding_status", "unverified"),
            "persistence_configured": persistence_configured,
            "limitations": [
                *([] if executor_callable else ["action_executor_unavailable"]),
                "persistence_not_verified" if persistence_configured else "persistence_client_unavailable",
            ],
        }

    def set_executor_binding_status(self, status: str) -> None:
        """Record lifecycle evidence for governed executor binding."""
        allowed = {"bound", "unavailable", "unverified"}
        self._executor_binding_status = status if status in allowed else "unverified"

    def check_health(self) -> Dict[str, Any]:
        """Sync health check with capability readiness separated from liveness."""
        try:
            readiness = self.readiness_report()
            return {
                "status": "healthy" if readiness["analysis_ready"] else "degraded",
                "readiness": readiness,
                "version": self.VERSION,
                "brands_tracked": len(self._observations),
                "decisions_made": len(self._decisions),
                "plans_created": len(self._plans),
                "goals_active": len([g for g in self._goals.values() if g.status == GoalStatus.ACTIVE]),
                "experiments_running": len([e for e in self._experiments.values() if e.status == "running"]),
                "strategies_registered": len(self._strategies),
                "decision_memory_size": len(self._decision_memory),
                "contexts_learned": len(self.context_learner.context_stats),
                "causal_chains_learned": len(self.context_learner.causal_chains),
                "timestamp": iso_now(),
            }
        except Exception as exc:
            return {"status": "degraded", "error": str(exc)[:200]}
    
    async def refresh_platform_state_context(self, max_age_seconds: int = 300) -> Dict[str, Any]:
        """Read the canonical Platform State without changing Condition Engine authority."""
        if isinstance(max_age_seconds, bool) or not isinstance(max_age_seconds, int) or max_age_seconds < 1:
            raise ValueError("max_age_seconds must be a positive integer")
        reader = self._platform_state_reader
        if reader is None or not callable(getattr(reader, "get_snapshot", None)):
            self._platform_state_context = {
                "status": "unavailable", "freshness": "unknown", "revision": 0,
                "services": [], "capabilities": [], "observer_observations": [],
                "reason": "platform_state_reader_not_configured",
            }
            return dict(self._platform_state_context)
        try:
            snapshot = await reader.get_snapshot()
        except Exception:
            logger.warning("ConditionEngine Platform State read failed closed", exc_info=True)
            self._platform_state_context = {
                "status": "unavailable", "freshness": "unknown", "revision": 0,
                "services": [], "capabilities": [], "observer_observations": [],
                "reason": "platform_state_read_failed",
            }
            return dict(self._platform_state_context)
        if snapshot is None:
            self._platform_state_context = {
                "status": "uninitialized", "freshness": "unknown", "revision": 0,
                "services": [], "capabilities": [], "observer_observations": [],
                "reason": "platform_state_snapshot_missing",
            }
            return dict(self._platform_state_context)

        updated_at = getattr(snapshot, "updated_at", None)
        now_utc = datetime.now(timezone.utc)
        if not isinstance(updated_at, datetime) or updated_at.tzinfo is None:
            freshness, status, reason = "invalid", "unknown", "platform_state_timestamp_invalid"
        else:
            age = (now_utc - updated_at.astimezone(timezone.utc)).total_seconds()
            if age < -30:
                freshness, status, reason = "invalid", "unknown", "platform_state_timestamp_in_future"
            elif age > max_age_seconds:
                freshness, status, reason = "stale", "stale", "platform_state_snapshot_expired"
            else:
                freshness, status, reason = "fresh", "available", None

        def readiness(item: Any) -> str:
            reported = getattr(getattr(item, "readiness", None), "value", str(getattr(item, "readiness", "unknown")))
            return reported if freshness == "fresh" else ("stale" if freshness == "stale" else "unknown")

        services = [
            {
                "service_id": str(getattr(item, "service_id", "unknown"))[:120],
                "readiness": readiness(item),
                "reported_readiness": getattr(getattr(item, "readiness", None), "value", str(getattr(item, "readiness", "unknown"))),
                "owner": str(getattr(item, "owner", "unknown"))[:120],
                "reason": str(getattr(item, "reason", "") or "")[:200] or None,
            }
            for item in list(getattr(snapshot, "services", ()) or ())[:100]
        ]
        capabilities = [
            {
                "capability_id": str(getattr(item, "capability_id", "unknown"))[:120],
                "readiness": readiness(item),
                "reported_readiness": getattr(getattr(item, "readiness", None), "value", str(getattr(item, "readiness", "unknown"))),
                "owner_service": str(getattr(item, "owner_service", "unknown"))[:120],
            }
            for item in list(getattr(snapshot, "capabilities", ()) or ())[:100]
        ]
        observer_items = list(getattr(snapshot, "observer_observations", ()) or ())
        observer_observations = []
        for item in observer_items[:100]:
            observed = getattr(item, "observed_at", None)
            reported = getattr(
                getattr(item, "readiness", None),
                "value",
                str(getattr(item, "readiness", "unknown")),
            )
            observer_freshness = freshness
            if freshness == "fresh":
                if not isinstance(observed, datetime) or observed.tzinfo is None:
                    observer_freshness = "unknown"
                else:
                    observer_age = (
                        now_utc - observed.astimezone(timezone.utc)
                    ).total_seconds()
                    if observer_age < -30:
                        observer_freshness = "unknown"
                    elif observer_age > max_age_seconds:
                        observer_freshness = "stale"
            effective = (
                reported if observer_freshness == "fresh"
                else ("stale" if observer_freshness == "stale" else "unknown")
            )
            observer_observations.append({
                "service_id": str(getattr(item, "service_id", "unknown"))[:120],
                "observer_id": str(getattr(item, "observer_id", "unknown"))[:80],
                "readiness": effective,
                "reported_readiness": reported,
                "freshness": observer_freshness,
                "observed_at": observed.isoformat() if isinstance(observed, datetime) else None,
                "source_version": str(getattr(item, "source_version", "unknown"))[:80],
            })

        self._platform_state_context = {
            "status": status, "freshness": freshness,
            "revision": int(getattr(snapshot, "revision", 0)),
            "schema_version": int(getattr(snapshot, "schema_version", 1)),
            "updated_at": updated_at.isoformat() if isinstance(updated_at, datetime) else None,
            "services": services, "capabilities": capabilities,
            "observer_observations": observer_observations,
            "observer_observation_count": len(observer_items),
            "observer_observations_truncated": len(observer_items) > len(observer_observations),
            "services_truncated": len(getattr(snapshot, "services", ()) or ()) > len(services),
            "capabilities_truncated": len(getattr(snapshot, "capabilities", ()) or ()) > len(capabilities),
            "reason": reason,
            "policy": {"read_only": True, "grants_execution_authority": False, "unknown_is_not_healthy": True},
        }
        return dict(self._platform_state_context)

    async def check_health_async(self) -> Dict[str, Any]:
        """Async health check with a separate read-only shared-state projection."""
        await self.refresh_platform_state_context()
        loop = asyncio.get_running_loop()
        health = await loop.run_in_executor(None, self.check_health)
        health["platform_state"] = dict(self._platform_state_context)
        return health
    
    # ═══════════════════════════════════════════════════════════════════════
    # WIRING
    # ═══════════════════════════════════════════════════════════════════════
    
    def set_action_executor(self, executor: Callable[[ActionPlan], Dict[str, Any]]) -> None:
        """Set action executor."""
        self.action_executor = executor
    
    def set_supabase_client(self, client: Any) -> None:
        """Set Supabase client and its read-only Platform State adapter."""
        self.supabase = client
        if client is None:
            self._platform_state_reader = None
            return
        try:
            from core.platform_state_reader import PlatformStateReader
            self._platform_state_reader = PlatformStateReader(client)
        except Exception:
            self._platform_state_reader = None
            logger.warning("ConditionEngine Platform State reader unavailable; shared state remains unknown")
    
    # ═══════════════════════════════════════════════════════════════════════
    # STRATEGIES
    # ═══════════════════════════════════════════════════════════════════════
    
    def _register_default_strategies(self) -> None:
        """Register default content strategies."""
        defaults = [
            Strategy(id="technical_tutorial", name="Technical Tutorials", description="Practical technical how-to content."),
            Strategy(id="technical_story", name="Technical Stories", description="Personal engineering experiences."),
            Strategy(id="technical_opinion", name="Technical Opinions", description="Strong engineering opinions."),
            Strategy(id="industry_news", name="Industry News", description="Relevant technology news."),
            Strategy(id="educational_tip", name="Educational Tips", description="Short practical technical lessons."),
        ]
        for strategy in defaults:
            self._strategies[strategy.id] = strategy
    
    # ═══════════════════════════════════════════════════════════════════════
    # OBSERVATION
    # ═══════════════════════════════════════════════════════════════════════
    
    def observe(self, observation: Observation) -> Dict[str, Any]:
        """Process observation and make decision."""
        observation.normalize()
        
        with self._lock:
            history = self._observations[observation.brand_id]
            history.append(observation)
            
            self.brand_clusterer.update_brand(
                observation.brand_id,
                {
                    "engagement_rate": observation.engagement_rate,
                    "click_through_rate": observation.click_through_rate,
                    "share_rate": observation.share_rate,
                    "comment_rate": observation.comment_rate,
                    "posting_hour": observation.posting_hour or 0,
                    "day_of_week": observation.day_of_week or 0,
                },
            )
            
            metrics = ["engagement", "engagement_rate", "click_through_rate"]
            baselines = {m: self._calculate_baseline(observation.brand_id, m) for m in metrics}
            topic_baseline = self._calculate_topic_baseline(observation.brand_id, observation.topic, "engagement")
            trend = self._calculate_trend(observation.brand_id, "engagement")
            anomaly = self._detect_anomaly(observation, baselines["engagement"])
            ratio = baselines["engagement"].ratio(observation.engagement)
            state = self._determine_state(observation.brand_id, ratio, trend, anomaly)
            confidence = self._calculate_confidence(observation, baselines["engagement"], trend, anomaly)
            
            # v5.1 — Adjust confidence using context learning
            if decision_candidate_action := self._candidate_action(state, trend, anomaly):
                confidence = self.context_learner.adjust_confidence(
                    confidence,
                    observation.brand_id,
                    observation.topic,
                    observation.content_type,
                    observation.style,
                    decision_candidate_action,
                )
            
            decision = self._make_decision(observation, baselines["engagement"], trend, anomaly, state, confidence)
            
            # v5.1 — Attach context to decision
            decision.context_key = self.context_learner._context_key(
                observation.brand_id, observation.topic, observation.content_type, observation.style
            )
            decision.time_bucket = time_bucket(observation.timestamp)
            
            # v5.1 — Apply context-based escalation/skip
            if decision.recommended_action:
                if self.context_learner.should_skip(
                    observation.brand_id,
                    observation.topic,
                    observation.content_type,
                    observation.style,
                    decision.recommended_action,
                ):
                    decision.status = ActionStatus.REJECTED
                    decision.reason += f" (skipped: unreliable context for {decision.recommended_action})"
                    decision.recommended_action = None
                elif self.context_learner.should_escalate(
                    observation.brand_id,
                    observation.topic,
                    observation.content_type,
                    observation.style,
                    decision.recommended_action,
                ):
                    decision.requires_approval = True
                    decision.reason += f" (escalated: low success rate for context)"
            
            # Council vote
            if self.council and decision.recommended_action:
                self._run_council_vote(observation, decision, baselines["engagement"])
            
            self._decisions[decision.id] = decision
            self._persist_decision(decision)
            
            return {
                "observation": self._serialize(observation),
                "baselines": self._serialize(baselines),
                "topic_baseline": self._serialize(topic_baseline),
                "trend": self._serialize(trend),
                "anomaly": self._serialize(anomaly),
                "state": state.value,
                "confidence": confidence,
                "decision": self._serialize(decision),
                "cluster_insights": self.brand_clusterer.get_cluster_insights(observation.brand_id),
                "context_insights": {
                    "context_key": decision.context_key,
                    "time_bucket": decision.time_bucket,
                    "context_success_rate": self.context_learner.get_context_success_rate(
                        observation.brand_id, observation.topic, observation.content_type, observation.style
                    ),
                } if decision.recommended_action else None,
            }
    
    def _candidate_action(self, state: DecisionState, trend: Trend, anomaly: Optional[Anomaly]) -> Optional[str]:
        """Predict which action would be recommended given state (for context adjustment)."""
        if state == DecisionState.CRITICAL:
            return "reduce_content_risk"
        if state == DecisionState.BOOST:
            return "increase_similar_content"
        if state == DecisionState.REDUCE:
            return "reduce_underperforming_content"
        if state == DecisionState.EXPERIMENT:
            return "run_experiment"
        if trend.direction == "rising":
            return "monitor_positive_trend"
        if trend.direction == "falling":
            if anomaly and anomaly.anomaly_type == AnomalyType.DIP:
                return "change_posting_time"
            if anomaly and anomaly.anomaly_type == AnomalyType.DRIFT:
                return "change_content_type"
            return "investigate_decline"
        return None
    
    # ═══════════════════════════════════════════════════════════════════════
    # BASELINES
    # ═══════════════════════════════════════════════════════════════════════
    
    def _calculate_baseline(self, brand_id: str, metric: str) -> Baseline:
        """Calculate baseline for metric."""
        key = (brand_id, metric)
        history = list(self._observations.get(brand_id, []))
        values = [self._metric_value(item, metric) for item in history]
        values = [float(v) for v in values if v is not None]
        values = values[-self.config.baseline_window:]
        
        baseline = Baseline(
            metric=metric,
            mean=round(safe_mean(values), 4),
            median=round(safe_median(values), 4),
            stddev=round(safe_stddev(values), 4),
            minimum=min(values) if values else 0.0,
            maximum=max(values) if values else 0.0,
            sample_count=len(values),
        )
        self._baselines[key] = baseline
        return baseline
    
    def _calculate_topic_baseline(self, brand_id: str, topic: str, metric: str) -> Optional[Baseline]:
        """Calculate per-topic baseline."""
        if not topic:
            return None
        
        key = (brand_id, topic, metric)
        history = [item for item in self._observations.get(brand_id, []) if item.topic == topic]
        values = [self._metric_value(item, metric) for item in history]
        values = [float(v) for v in values if v is not None]
        values = values[-self.config.baseline_window:]
        
        if not values:
            return None
        
        baseline = Baseline(
            metric=metric,
            mean=round(safe_mean(values), 4),
            median=round(safe_median(values), 4),
            stddev=round(safe_stddev(values), 4),
            minimum=min(values),
            maximum=max(values),
            sample_count=len(values),
        )
        self._topic_baselines[key] = baseline
        return baseline
    
    # ═══════════════════════════════════════════════════════════════════════
    # TREND ANALYSIS
    # ═══════════════════════════════════════════════════════════════════════
    
    def _calculate_trend(self, brand_id: str, metric: str) -> Trend:
        """Calculate trend with prediction."""
        history = list(self._observations.get(brand_id, []))
        values = [self._metric_value(item, metric) for item in history]
        values = [float(v) for v in values if v is not None]
        
        if len(values) < 3:
            trend = Trend(metric=metric, confidence=0.1, sample_count=len(values))
            self._trends[(brand_id, metric)] = trend
            return trend
        
        recent = values[-5:]
        first, last = recent[0], recent[-1]
        velocity = 0.0 if first == 0 else (last - first) / abs(first)
        
        midpoint = len(recent) // 2
        first_half, second_half = recent[:midpoint], recent[midpoint:]
        first_mean = safe_mean(first_half)
        second_mean = safe_mean(second_half)
        acceleration = 0.0 if first_mean == 0 else (second_mean - first_mean) / abs(first_mean)
        
        direction = (
            "rising" if velocity >= self.config.strong_positive_velocity
            else "falling" if velocity <= self.config.strong_negative_velocity
            else "stable"
        )
        
        confidence = clamp(len(values) / self.config.baseline_window)
        predicted_next = self.trend_analyzer.predict_next_value(values, horizon=3)
        predicted_direction = (
            "rising" if predicted_next > values[-1]
            else "falling" if predicted_next < values[-1]
            else "stable"
        )
        reversal_prob = self.trend_analyzer.predict_reversal_probability(recent, direction)
        
        trend = Trend(
            metric=metric,
            velocity=round(velocity, 4),
            acceleration=round(acceleration, 4),
            direction=direction,
            confidence=round(confidence, 4),
            sample_count=len(values),
            predicted_next_value=round(predicted_next, 4),
            predicted_direction=predicted_direction,
            reversal_probability=round(reversal_prob, 4),
        )
        self._trends[(brand_id, metric)] = trend
        return trend
    
    # ═══════════════════════════════════════════════════════════════════════
    # ANOMALY DETECTION
    # ═══════════════════════════════════════════════════════════════════════
    
    def _detect_anomaly(self, observation: Observation, baseline: Baseline) -> Optional[Anomaly]:
        """Detect and classify anomaly."""
        if baseline.sample_count < self.config.minimum_anomaly_samples:
            return None
        
        z = baseline.z_score(observation.engagement)
        if abs(z) < self.config.anomaly_z_score:
            return None
        
        severity = "positive" if z >= 0 else "negative"
        confidence = clamp(abs(z) / 6.0)
        
        history = list(self._observations.get(observation.brand_id, []))
        recent_values = [item.engagement for item in history[-10:]]
        anomaly_type = self.anomaly_classifier.classify(observation.engagement, baseline, recent_values)
        hypothesis = self.anomaly_classifier.generate_hypothesis(anomaly_type, observation)
        
        return Anomaly(
            metric="engagement",
            value=observation.engagement,
            expected=baseline.mean,
            z_score=round(z, 4),
            severity=severity,
            confidence=round(confidence, 4),
            anomaly_type=anomaly_type,
            root_cause_hypothesis=hypothesis,
        )
    
    # ═══════════════════════════════════════════════════════════════════════
    # CONFIDENCE
    # ═══════════════════════════════════════════════════════════════════════
    
    def _calculate_confidence(self, observation: Observation, baseline: Baseline, trend: Trend, anomaly: Optional[Anomaly]) -> float:
        """Calculate decision confidence."""
        components = [
            clamp(baseline.sample_count / 30),
            clamp(1.0 - (baseline.stddev / baseline.mean)) if baseline.mean > 0 else 0.2,
            trend.confidence,
            anomaly.confidence if anomaly else 0.5,
        ]
        
        checks = [bool(observation.topic), bool(observation.content_type), bool(observation.style), observation.posting_hour is not None]
        components.append(sum(checks) / len(checks) if checks else 0.0)
        
        raw = safe_mean(components)
        calibrated = self._calibrate_confidence(observation.brand_id, raw)
        return round(calibrated, 4)
    
    def _calibrate_confidence(self, brand_id: str, raw: float) -> float:
        """Calibrate confidence based on history."""
        if not self.config.enable_confidence_calibration:
            return raw
        
        history = self._confidence_history.get(brand_id, [])
        if len(history) < 5:
            return raw
        
        recent = list(history)[-10:]
        correct = sum(1 for item in recent if item["success"] and item["confidence"] >= 0.65)
        accuracy = correct / len(recent) if recent else 0.5
        
        factor = clamp(accuracy / max(raw, 0.01), 0.7, 1.3)
        return clamp(raw * factor, 0, 1)
    
    # ═══════════════════════════════════════════════════════════════════════
    # STATE MACHINE
    # ═══════════════════════════════════════════════════════════════════════
    
    def _determine_state(self, brand_id: str, ratio: float, trend: Trend, anomaly: Optional[Anomaly]) -> DecisionState:
        """Determine decision state."""
        current = self._states.get(brand_id, DecisionState.OBSERVE)
        
        if ratio <= self.config.critical_performance_ratio:
            new_state = DecisionState.CRITICAL
        elif ratio >= self.config.boost_enter_threshold:
            new_state = DecisionState.BOOST
        elif current == DecisionState.BOOST and ratio >= self.config.boost_exit_threshold:
            new_state = DecisionState.BOOST
        elif ratio <= self.config.reduce_enter_threshold:
            new_state = DecisionState.REDUCE
        elif current == DecisionState.REDUCE and ratio <= self.config.reduce_exit_threshold:
            new_state = DecisionState.REDUCE
        elif self.config.enable_automated_experiments and trend.direction == "stable" and trend.reversal_probability > 0.5:
            new_state = DecisionState.EXPERIMENT
        elif trend.direction in ("rising", "falling"):
            new_state = DecisionState.MONITOR
        else:
            new_state = DecisionState.OBSERVE
        
        self._states[brand_id] = new_state
        return new_state
    
    # ═══════════════════════════════════════════════════════════════════════
    # DECISION ENGINE
    # ═══════════════════════════════════════════════════════════════════════
    
    def _make_decision(self, observation: Observation, baseline: Baseline, trend: Trend, anomaly: Optional[Anomaly], state: DecisionState, confidence: float) -> Decision:
        """Make autonomous decision."""
        ratio = baseline.ratio(observation.engagement)
        evidence = {
            "workspace_id": str(observation.metadata.get("workspace_id") or ""),
            "engagement": observation.engagement,
            "engagement_rate": observation.engagement_rate,
            "click_through_rate": observation.click_through_rate,
            "share_rate": observation.share_rate,
            "comment_rate": observation.comment_rate,
            "baseline": baseline.mean,
            "engagement_ratio": round(ratio, 4),
            "trend": {"direction": trend.direction, "velocity": trend.velocity, "predicted_next": trend.predicted_next_value, "reversal_probability": trend.reversal_probability},
            "sample_count": baseline.sample_count,
        }
        
        if anomaly:
            evidence["anomaly"] = {"z_score": anomaly.z_score, "severity": anomaly.severity, "type": anomaly.anomaly_type.value, "root_cause": anomaly.root_cause_hypothesis}
        
        if state == DecisionState.CRITICAL:
            action, reason, risk = "reduce_content_risk", "Performance critically below baseline.", ActionRisk.MEDIUM
        elif state == DecisionState.BOOST:
            action, reason, risk = "increase_similar_content", "Performance significantly exceeds baseline.", ActionRisk.LOW
        elif state == DecisionState.REDUCE:
            action, reason, risk = "reduce_underperforming_content", "Performance materially below baseline.", ActionRisk.MEDIUM
        elif state == DecisionState.EXPERIMENT:
            action, reason, risk = "run_experiment", "Stable performance — testing new strategies.", ActionRisk.LOW
        elif trend.direction == "rising":
            action, reason, risk = "monitor_positive_trend", "Positive momentum detected.", ActionRisk.LOW
        elif trend.direction == "falling":
            if anomaly and anomaly.anomaly_type == AnomalyType.DIP:
                action, reason, risk = "change_posting_time", "Single dip — testing different posting time.", ActionRisk.LOW
            elif anomaly and anomaly.anomaly_type == AnomalyType.DRIFT:
                action, reason, risk = "change_content_type", "Gradual decline — testing new content type.", ActionRisk.LOW
            else:
                action, reason, risk = "investigate_decline", "Negative momentum detected.", ActionRisk.LOW
        else:
            action, reason, risk = None, "Insufficient evidence for intervention.", ActionRisk.LOW
        
        requires_approval = risk in {ActionRisk.HIGH, ActionRisk.CRITICAL} or confidence < self.config.minimum_confidence_for_action
        
        return Decision(
            id=generate_id("decision"),
            brand_id=observation.brand_id,
            state=state,
            confidence=confidence,
            reason=reason,
            evidence=evidence,
            recommended_action=action,
            risk=risk,
            requires_approval=requires_approval,
        )
    
    # ═══════════════════════════════════════════════════════════════════════
    # COUNCIL VOTING
    # ═══════════════════════════════════════════════════════════════════════
    
    def _run_council_vote(self, observation: Observation, decision: Decision, baseline: Baseline) -> Dict[str, Any]:
        """Run council vote on decision."""
        if not self.council:
            return {"decision": "APPROVED", "reason": "No council"}
        
        brand_context = {"industry": observation.metadata.get("industry", "general"), "brand_id": observation.brand_id}
        proposal = {
            "topic": observation.topic or "general",
            "content": decision.reason,
            "hour": observation.posting_hour or datetime.now().hour,
            "day": datetime.now().strftime("%A").lower(),
            "engagement_ratio": baseline.ratio(observation.engagement) if baseline else 1.0,
            "sample_count": baseline.sample_count if baseline else 0,
            "has_cta": observation.has_cta or "call" in decision.reason.lower(),
            "system_health": 80,
            "topic_source": "condition_engine",
        }
        
        vote_result = self.council.vote_on(brand_context, proposal)
        
        if vote_result["decision"] == "REJECTED":
            decision.recommended_action = None
            decision.reason = f"Cancelled by council: {vote_result['reason']}"
        elif vote_result["decision"] == "NEEDS_REVISION":
            decision.requires_approval = True
            decision.reason += f" (Council: {vote_result['approve_ratio']:.0%} approval)"
        
        logger.info("Council vote for %s: %s (%.0f%% approval)", decision.id, vote_result["decision"], vote_result["approve_ratio"] * 100)
        return vote_result
    
    # ═══════════════════════════════════════════════════════════════════════
    # ACTION PLANNING
    # ═══════════════════════════════════════════════════════════════════════
    
    def get_decision(self, decision_id: str) -> Optional[Decision]:
        """Return an internal decision by id without exposing mutable storage."""
        decision = self._decisions.get(str(decision_id))
        return decision

    def create_action_plan(self, decision: Decision) -> Optional[ActionPlan]:
        """Create action plan from decision."""
        if not decision.recommended_action:
            return None
        if self._is_on_cooldown(decision.brand_id, decision.recommended_action):
            return None
        if not self._is_action_allowed(decision.risk, decision.confidence):
            return None
        
        plan = ActionPlan(
            id=generate_id("plan"),
            decision_id=decision.id,
            brand_id=decision.brand_id,
            action=decision.recommended_action,
            risk=decision.risk,
            confidence=decision.confidence,
            parameters=self._build_action_parameters(decision),
            expected_outcome=self._estimate_expected_outcome(decision),
        )
        self._plans[plan.id] = plan
        return plan
    
    def _build_action_parameters(self, decision: Decision) -> Dict[str, Any]:
        """Build action parameters."""
        action = decision.recommended_action
        params = {
            "increase_similar_content": {"content_mix_change": 0.20, "preserve_topic": True, "preserve_style": True, "duration_posts": 5},
            "reduce_underperforming_content": {"content_mix_change": -0.20, "duration_posts": 5},
            "reduce_content_risk": {"content_mix_change": -0.30, "experimental_content": False, "duration_posts": 5},
            "investigate_decline": {"run_experiment": True, "collect_more_data": True},
            "monitor_positive_trend": {"increase_observation_frequency": True},
            "run_experiment": {"experiment_type": "hook_variation", "control": "direct_technical_hook", "variant": "story_based_hook", "duration_posts": 10},
            "change_posting_time": {"test_new_hours": True, "duration_posts": 5},
            "change_content_type": {"test_new_format": True, "duration_posts": 5},
        }
        built = params.get(action, {})
        if built:
            context = {"correlation_id": decision.correlation_id}
            workspace_id = decision.evidence.get("workspace_id")
            if workspace_id:
                context["workspace_id"] = str(workspace_id)
            built = {**built, **context}
        return built
    
    def _estimate_expected_outcome(self, decision: Decision) -> Dict[str, Any]:
        """Estimate expected outcome."""
        baseline = decision.evidence.get("baseline", 0)
        ratio = decision.evidence.get("engagement_ratio", 1.0)
        action = decision.recommended_action
        
        targets = {
            "increase_similar_content": 1.10,
            "reduce_underperforming_content": 0.95,
            "reduce_content_risk": 0.85,
            "run_experiment": 1.05,
            "change_posting_time": 1.02,
            "change_content_type": 1.02,
        }
        target = baseline * targets.get(action, 1.0)
        
        return {"metric": "engagement", "baseline": baseline, "current_ratio": ratio, "target": round(target, 2)}
    
    # ═══════════════════════════════════════════════════════════════════════
    # SAFETY & COOLDOWNS
    # ═══════════════════════════════════════════════════════════════════════
    
    def _is_action_allowed(self, risk: ActionRisk, confidence: float) -> bool:
        """Check if action is allowed."""
        if confidence < self.config.minimum_confidence_for_action:
            return False
        return {
            ActionRisk.LOW: self.config.allow_low_risk_autonomy,
            ActionRisk.MEDIUM: self.config.allow_medium_risk_autonomy,
            ActionRisk.HIGH: self.config.allow_high_risk_autonomy,
            ActionRisk.CRITICAL: self.config.allow_critical_autonomy,
        }.get(risk, False)
    
    def _is_on_cooldown(self, brand_id: str, action: str) -> bool:
        """Check cooldown."""
        key = (brand_id, action)
        last = self._last_action_at.get(key)
        if not last:
            return False
        if action.startswith("increase"):
            minutes = self.config.boost_cooldown_minutes
        elif action.startswith("reduce"):
            minutes = self.config.reduce_cooldown_minutes
        elif action == "run_experiment":
            minutes = self.config.experiment_cooldown_minutes
        else:
            minutes = self.config.default_action_cooldown_minutes
        return (utc_now() - last) < timedelta(minutes=minutes)
    
    # ═══════════════════════════════════════════════════════════════════════
    # EXECUTION
    # ═══════════════════════════════════════════════════════════════════════
    
    def execute_plan(self, plan_id: str, approved: bool = False) -> ActionPlan:
        """Execute a plan from synchronous runtimes."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.execute_plan_async(plan_id, approved=approved))
        raise RuntimeError("execute_plan() cannot run inside an active event loop; use execute_plan_async()")
    
    async def execute_plan_async(self, plan_id: str, approved: bool = False) -> ActionPlan:
        """Async-native plan execution supporting async action executors."""
        plan = self._plans.get(plan_id)
        if not plan:
            raise ValueError(f"Unknown action plan: {plan_id}")
        event_context = {
            "plan_id": plan.id,
            "decision_id": plan.decision_id,
            "brand_id": plan.brand_id,
        }
        for context_key in ("workspace_id", "correlation_id", "parent_event_id"):
            context_value = (plan.parameters or {}).get(context_key)
            if context_value not in (None, ""):
                event_context[context_key] = str(context_value)
        if plan.risk in {ActionRisk.HIGH, ActionRisk.CRITICAL} and not approved:
            plan.status = ActionStatus.REJECTED
            return plan
        if self.config.dry_run:
            plan.status = ActionStatus.APPROVED
            return plan
        if not self.action_executor:
            plan.status = ActionStatus.FAILED
            plan.outcome = {"error": "action_executor_unavailable"}
            _publish_event("strategy_executed", {
                **event_context,
                "action": plan.action,
                "success": False,
                "error": "action_executor_unavailable",
            })
            return plan
        
        try:
            result = self.action_executor(plan)
            if asyncio.iscoroutine(result) or asyncio.isfuture(result):
                result = await result
            plan.outcome = result
            if isinstance(result, dict):
                for result_key in ("execution_id", "intent_id"):
                    result_value = result.get(result_key)
                    if result_value not in (None, ""):
                        event_context[result_key] = str(result_value)
            result_ok = True
            if isinstance(result, dict) and "ok" in result:
                result_ok = bool(result.get("ok"))
            elif isinstance(result, bool):
                result_ok = result
            if not result_ok:
                plan.status = ActionStatus.FAILED
                _publish_event("strategy_executed", {
                    **event_context,
                    "action": plan.action,
                    "success": False,
                    "status": (result.get("status") if isinstance(result, dict) else "failed"),
                    "error": (result.get("error") if isinstance(result, dict) else "action_result_not_ok"),
                })
                return plan
            plan.status = ActionStatus.EXECUTED
            plan.executed_at = utc_now()
            self._last_action_at[(plan.brand_id, plan.action)] = utc_now()
            _publish_event("strategy_executed", {
                **event_context,
                "action": plan.action,
                "success": True,
                "status": (result.get("status") if isinstance(result, dict) else "succeeded"),
            })
            return plan
        except Exception as exc:
            plan.status = ActionStatus.FAILED
            plan.outcome = {"error": str(exc)}
            _publish_event("strategy_executed", {
                **event_context,
                "action": plan.action,
                "success": False,
                "status": "failed",
                "error": str(exc),
            })
            return plan
    
    # ═══════════════════════════════════════════════════════════════════════
    # GOALS
    # ═══════════════════════════════════════════════════════════════════════
    
    def register_goal(self, goal: Goal) -> Goal:
        """Register goal."""
        self._goals[goal.id] = goal
        return goal
    
    def evaluate_goals(self, brand_id: str, metrics: Dict[str, float]) -> List[Dict[str, Any]]:
        """Evaluate goals."""
        results = []
        for goal in self._goals.values():
            if goal.brand_id != brand_id or goal.status not in {GoalStatus.ACTIVE, GoalStatus.AT_RISK}:
                continue
            current = metrics.get(goal.metric)
            if current is None:
                continue
            progress = goal.calculate_progress(current)
            if progress >= 1.0:
                goal.status = GoalStatus.ACHIEVED
            elif progress < 0.25:
                goal.status = GoalStatus.AT_RISK
            results.append({"goal_id": goal.id, "name": goal.name, "metric": goal.metric, "current": current, "target": goal.target_value, "progress": progress, "status": goal.status.value})
        return results
    
    # ═══════════════════════════════════════════════════════════════════════
    # BRAND HEALTH
    # ═══════════════════════════════════════════════════════════════════════
    
    def calculate_brand_health(self, brand_id: str) -> Dict[str, Any]:
        """Calculate brand health."""
        observations = list(self._observations.get(brand_id, []))
        if not observations:
            health = {"score": 50.0, "status": "unknown", "components": {}}
            self._brand_health[brand_id] = health
            _publish_event("brand_health_updated", {"brand_id": brand_id, "health_score": health["score"]})
            return health
        
        metrics = ["engagement", "engagement_rate", "click_through_rate"]
        component_scores = {}
        
        for metric in metrics:
            baseline = self._calculate_baseline(brand_id, metric)
            trend = self._calculate_trend(brand_id, metric)
            recent = [self._metric_value(item, metric) for item in observations[-10:]]
            recent_average = safe_mean(recent)
            performance = clamp(recent_average / baseline.mean / 2) * 100 if baseline.mean > 0 else 50
            trend_score = 85 if trend.direction == "rising" else 25 if trend.direction == "falling" else 50
            component_scores[metric] = {"performance": round(performance, 2), "trend": round(trend_score, 2), "combined": round(performance * 0.6 + trend_score * 0.4, 2)}
        
        weights = {"engagement": 0.4, "engagement_rate": 0.3, "click_through_rate": 0.3}
        overall = sum(component_scores[m]["combined"] * weights[m] for m in metrics)
        score = round(clamp(overall, 0, 100), 2)
        status = "healthy" if score >= 80 else "stable" if score >= 60 else "watch" if score >= 40 else "weak" if score >= 20 else "critical"
        
        health = {"score": score, "status": status, "components": component_scores, "cluster_insights": self.brand_clusterer.get_cluster_insights(brand_id), "updated_at": iso_now()}
        self._brand_health[brand_id] = health
        _publish_event("brand_health_updated", {"brand_id": brand_id, "health_score": score})
        return health
    
    # ═══════════════════════════════════════════════════════════════════════
    # STRATEGY SELECTION
    # ═══════════════════════════════════════════════════════════════════════
    
    def select_strategy(self, brand_id: str, topic: str = "", content_type: str = "", style: str = "") -> Optional[Strategy]:
        """Select a strategy using UCB, scoped to brand + context (v5.1)."""
        scoped = []
        for strategy in self._strategies.values():
            if not strategy.enabled:
                continue
            strategy_brand = strategy.metadata.get("brand_id") if strategy.metadata else None
            if strategy_brand is None or strategy_brand == brand_id:
                scoped.append(strategy)
        strategies = scoped
        if not strategies:
            return None
        
        # v5.1 — Filter out strategies with critically low context success rate
        if topic or content_type or style:
            filtered = []
            ctx_key = self.context_learner._context_key(brand_id, topic, content_type, style)
            for s in strategies:
                ctx_rate = s.context_success_rate(ctx_key)
                if ctx_rate is not None and ctx_rate < self.config.critical_skip_threshold:
                    continue
                filtered.append(s)
            if filtered:
                strategies = filtered
        
        total_trials = sum(s.trials for s in strategies)
        if random.random() < self.config.exploration_rate:
            return random.choice(strategies)
        
        # v5.1 — Use context-weighted UCB
        def context_aware_score(s: Strategy) -> float:
            base = s.ucb_score(total_trials)
            if not (topic or content_type or style):
                return base
            
            ctx_key = self.context_learner._context_key(brand_id, topic, content_type, style)
            ctx_rate = s.context_success_rate(ctx_key)
            time_rate = s.time_success_rate(time_bucket())
            recent_rate = s.recent_success_rate()
            
            scores = [base]
            weights = [0.5]
            if ctx_rate is not None:
                scores.append(ctx_rate)
                weights.append(0.25)
            if time_rate is not None:
                scores.append(time_rate)
                weights.append(0.15)
            if recent_rate is not None:
                scores.append(recent_rate)
                weights.append(0.10)
            
            total_w = sum(weights)
            return sum(s_ * w for s_, w in zip(scores, weights)) / total_w
        
        return max(strategies, key=context_aware_score)
    
    def record_strategy_result(
        self,
        strategy_id: str,
        reward: float,
        success: bool,
        brand_id: str = "",
        topic: str = "",
        content_type: str = "",
        style: str = "",
        duration_ms: float = 0.0,
        dt: Optional[datetime] = None,
    ) -> None:
        """Record strategy result with context (v5.1)."""
        strategy = self._strategies.get(strategy_id)
        if not strategy:
            return
        
        dt = dt or utc_now()
        
        # Global
        strategy.trials += 1
        if success:
            strategy.successes += 1
        else:
            strategy.failures += 1
        strategy.reward = (1 - self.config.learning_rate) * strategy.reward + self.config.learning_rate * reward
        
        # v5.1 — Context tracking
        if brand_id and (topic or content_type or style):
            ctx_key = self.context_learner._context_key(brand_id, topic, content_type, style)
            strategy.context_stats[ctx_key]["successes" if success else "failures"] += 1
        
        # v5.1 — Time tracking
        tb = time_bucket(dt)
        strategy.time_stats[tb]["successes" if success else "failures"] += 1
        
        # v5.1 — Recent outcomes for decay-weighted rate
        strategy.recent_outcomes.append({
            "success": success,
            "reward": reward,
            "timestamp": dt.isoformat(),
            "duration_ms": duration_ms,
            "context": ctx_key if brand_id and (topic or content_type or style) else None,
            "time_bucket": tb,
        })
    
    # ═══════════════════════════════════════════════════════════════════════
    # EXPERIMENTS
    # ═══════════════════════════════════════════════════════════════════════
    
    def create_experiment(self, brand_id: str, name: str, variable: str, control: str, variant: str, auto_deploy: bool = True) -> Experiment:
        """Create experiment."""
        experiment = Experiment(id=generate_id("experiment"), brand_id=brand_id, name=name, variable=variable, control=control, variant=variant, auto_deploy_winner=auto_deploy)
        self._experiments[experiment.id] = experiment
        return experiment
    
    def record_experiment_result(self, experiment_id: str, variant: str, result: float) -> Optional[Dict[str, Any]]:
        """Record experiment result."""
        experiment = self._experiments.get(experiment_id)
        if not experiment:
            return None
        if variant == "control":
            experiment.control_results.append(float(result))
        elif variant == "variant":
            experiment.variant_results.append(float(result))
        else:
            raise ValueError("variant must be 'control' or 'variant'")
        return self.evaluate_experiment(experiment_id)
    
    def evaluate_experiment(self, experiment_id: str) -> Dict[str, Any]:
        """Evaluate experiment."""
        experiment = self._experiments.get(experiment_id)
        if not experiment:
            raise ValueError(f"Unknown experiment: {experiment_id}")
        
        control, variant = experiment.control_results, experiment.variant_results
        if len(control) < experiment.minimum_samples or len(variant) < experiment.minimum_samples:
            return {"status": "insufficient_data", "control_samples": len(control), "variant_samples": len(variant)}
        
        control_mean, variant_mean = safe_mean(control), safe_mean(variant)
        improvement = 0.0 if control_mean == 0 else (variant_mean - control_mean) / abs(control_mean)
        winner = "variant" if improvement > 0.05 else "control" if improvement < -0.05 else "inconclusive"
        experiment.winner = winner
        
        if winner != "inconclusive":
            experiment.status = "completed"
            if experiment.auto_deploy_winner and not experiment.deployed:
                self._deploy_experiment_winner(experiment)
                experiment.deployed = True
        
        return {"status": experiment.status, "winner": winner, "control_mean": control_mean, "variant_mean": variant_mean, "improvement": round(improvement, 4), "control_samples": len(control), "variant_samples": len(variant), "deployed": experiment.deployed}
    
    def _deploy_experiment_winner(self, experiment: Experiment) -> None:
        """Deploy winning variant."""
        logger.info("Experiment %s: deploying %s", experiment.id, experiment.winner)
        
        decision = Decision(
            id=generate_id("decision"), brand_id=experiment.brand_id, state=DecisionState.EXPERIMENT,
            confidence=0.9, reason=f"Experiment {experiment.id} winner: {experiment.winner}",
            evidence={"experiment_id": experiment.id, "winner": experiment.winner, "variable": experiment.variable},
            recommended_action="apply_experiment_winner", risk=ActionRisk.LOW,
        )
        self._decisions[decision.id] = decision
        
        plan = ActionPlan(
            id=generate_id("plan"), decision_id=decision.id, brand_id=experiment.brand_id,
            action="apply_experiment_winner", risk=ActionRisk.LOW, confidence=0.9,
            parameters={"experiment_id": experiment.id, "winner": experiment.winner, "variable": experiment.variable, "variant_value": experiment.variant if experiment.winner == "variant" else experiment.control},
        )
        self._plans[plan.id] = plan
    
    # ═══════════════════════════════════════════════════════════════════════
    # LEARNING
    # ═══════════════════════════════════════════════════════════════════════
    
    def record_decision_outcome(self, decision_id: str, actual_value: float) -> Optional[DecisionMemory]:
        """Record decision outcome (enhanced v5.1)."""
        decision = self._decisions.get(decision_id)
        if not decision:
            return None
        
        expected = decision.evidence.get("baseline", 0)
        action = decision.recommended_action
        success = actual_value >= expected * (1.10 if action == "increase_similar_content" else 0.95 if action in {"reduce_underperforming_content", "reduce_content_risk"} else 1.0)
        
        memory = DecisionMemory(
            decision_id=decision_id, brand_id=decision.brand_id,
            action=action or "observe", reason=decision.reason,
            confidence=decision.confidence, expected_value=expected,
            actual_value=actual_value, success=success, evaluated_at=utc_now(),
        )
        self._decision_memory.append(memory)
        self._confidence_history[decision.brand_id].append({"confidence": decision.confidence, "success": success, "timestamp": utc_now()})
        self._update_thresholds_from_outcome(decision, memory)
        self._learn_from_decision(memory, decision)
        self._persist_decision_memory(memory)
        return memory
    
    def _update_thresholds_from_outcome(self, decision: Decision, memory: DecisionMemory) -> None:
        """Update thresholds based on outcome."""
        if not memory.success:
            return
        ratio = decision.evidence.get("engagement_ratio", 1.0)
        if decision.recommended_action == "increase_similar_content":
            self.threshold_manager.update_threshold("boost_enter_threshold", ratio, "true_positive" if ratio > self.config.boost_enter_threshold else "false_positive")
        elif decision.recommended_action == "reduce_underperforming_content":
            self.threshold_manager.update_threshold("reduce_enter_threshold", ratio, "true_positive" if ratio < self.config.reduce_enter_threshold else "false_positive")
    
    def _learn_from_decision(self, memory: DecisionMemory, decision: Decision) -> None:
        """Learn from decision outcome with full context (v5.1)."""
        if memory.actual_value is None or memory.expected_value <= 0:
            return
        
        reward = clamp(memory.actual_value / memory.expected_value, 0, 2)
        
        # v5.1 — Record context outcome
        # Extract context from decision
        ctx_key = decision.context_key
        if ctx_key and self.config.enable_context_learning:
            parts = ctx_key.split("|")
            if len(parts) >= 4:
                brand_id, topic, ctype, style = parts[0], parts[1], parts[2], parts[3]
                self.context_learner.record_outcome(
                    brand_id=brand_id,
                    topic="" if topic == "none" else topic,
                    content_type="" if ctype == "none" else ctype,
                    style="" if style == "none" else style,
                    action=memory.action,
                    success=bool(memory.success),
                )
        
        strategy_map = {
            "increase_similar_content": "technical_tutorial",
            "reduce_underperforming_content": "technical_tutorial",
            "change_content_type": "technical_story",
            "change_posting_time": "educational_tip",
            "run_experiment": "technical_opinion",
        }
        strategy_id = strategy_map.get(memory.action)
        if strategy_id:
            strategy = self._strategies.get(strategy_id)
            if strategy is not None:
                strategy_brand = strategy.metadata.get("brand_id") if strategy.metadata else None
                if strategy_brand in (None, memory.brand_id):
                    parts = decision.context_key.split("|") if decision.context_key else []
                    self.record_strategy_result(
                        strategy_id=strategy_id,
                        reward=reward,
                        success=bool(memory.success),
                        brand_id=memory.brand_id,
                        topic=parts[1] if len(parts) > 1 and parts[1] != "none" else "",
                        content_type=parts[2] if len(parts) > 2 and parts[2] != "none" else "",
                        style=parts[3] if len(parts) > 3 and parts[3] != "none" else "",
                    )
    
    # ═══════════════════════════════════════════════════════════════════════
    # AUTONOMOUS CYCLE
    # ═══════════════════════════════════════════════════════════════════════
    
    def run_cycle(self, observations: Iterable[Observation]) -> Dict[str, Any]:
        """Run full autonomous cycle."""
        results = []
        grouped: Dict[str, List[Observation]] = defaultdict(list)
        
        for observation in observations:
            grouped[observation.brand_id].append(observation)
            results.append(self.observe(observation))
        
        brand_reports = {}
        for brand_id in grouped:
            health = self.calculate_brand_health(brand_id)
            goals = self.evaluate_goals(brand_id, {"engagement": safe_mean([item.engagement for item in grouped[brand_id]])})
            strategy = self.select_strategy(brand_id)
            brand_reports[brand_id] = {"health": health, "goals": goals, "recommended_strategy": self._serialize(strategy) if strategy else None}
        
        return {"timestamp": iso_now(), "observations_processed": len(results), "results": results, "brands": brand_reports}
    
    # ═══════════════════════════════════════════════════════════════════════
    # STATUS
    # ═══════════════════════════════════════════════════════════════════════
    
    def explain_decision(self, decision_id: str) -> Dict[str, Any]:
        """Explain a decision."""
        decision = self._decisions.get(decision_id)
        if not decision:
            return {"error": "Decision not found"}
        return {"decision_id": decision.id, "brand_id": decision.brand_id, "state": decision.state.value, "confidence": decision.confidence, "reason": decision.reason, "recommended_action": decision.recommended_action, "risk": decision.risk.value, "requires_approval": decision.requires_approval, "evidence": decision.evidence, "created_at": decision.created_at.isoformat(), "context_key": decision.context_key, "time_bucket": decision.time_bucket}
    
    def get_status(self, brand_id: Optional[str] = None) -> Dict[str, Any]:
        """Get engine status."""
        if brand_id:
            observations = list(self._observations.get(brand_id, []))
            return {"brand_id": brand_id, "state": self._states.get(brand_id, DecisionState.OBSERVE).value, "health": self._brand_health.get(brand_id), "observation_count": len(observations), "active_goals": len([g for g in self._goals.values() if g.brand_id == brand_id and g.status == GoalStatus.ACTIVE]), "active_experiments": len([e for e in self._experiments.values() if e.brand_id == brand_id and e.status == "running"]), "cluster_insights": self.brand_clusterer.get_cluster_insights(brand_id)}
        
        return {"brands": len(self._observations), "observations": sum(len(items) for items in self._observations.values()), "decisions": len(self._decisions), "plans": len(self._plans), "goals": len(self._goals), "experiments": len(self._experiments), "strategies": len(self._strategies), "decision_memory": len(self._decision_memory), "dry_run": self.config.dry_run, "adaptive_thresholds": {"boost_enter": self.config.boost_enter_threshold, "reduce_enter": self.config.reduce_enter_threshold, "critical_performance": self.config.critical_performance_ratio}, "learning": self.context_learner.get_learning_report(), "timestamp": iso_now()}
    
    # ═══════════════════════════════════════════════════════════════════════
    # UTILITIES
    # ═══════════════════════════════════════════════════════════════════════
    
    @staticmethod
    def _metric_value(observation: Observation, metric: str) -> float:
        """Get metric value from observation."""
        try:
            return float(getattr(observation, metric, 0.0))
        except Exception:
            return 0.0
    
    @staticmethod
    def _serialize(value: Any) -> Any:
        """Serialize value for JSON."""
        if value is None:
            return None
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, deque):
            return [AutonomousConditionEngine._serialize(item) for item in value]
        if hasattr(value, "__dataclass_fields__"):
            return {key: AutonomousConditionEngine._serialize(item) for key, item in asdict(value).items()}
        if isinstance(value, dict):
            return {str(k): AutonomousConditionEngine._serialize(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [AutonomousConditionEngine._serialize(item) for item in value]
        return value
    
    # ═══════════════════════════════════════════════════════════════════════
    # PERSISTENCE
    # ═══════════════════════════════════════════════════════════════════════
    
    def _persist_decision(self, decision: Decision) -> None:
        """Persist decision to Supabase."""
        if not self.supabase or not self.config.persist_decisions:
            return
        try:
            self.supabase.table("autonomous_decisions").upsert({"id": decision.id, "brand_id": decision.brand_id, "state": decision.state.value, "confidence": decision.confidence, "reason": decision.reason, "recommended_action": decision.recommended_action, "risk": decision.risk.value, "requires_approval": decision.requires_approval, "evidence": decision.evidence, "created_at": decision.created_at.isoformat()}).execute()
        except Exception as exc:
            logger.warning("Failed to persist decision: %s", exc)
    
    def _persist_decision_memory(self, memory: DecisionMemory) -> None:
        """Persist decision memory."""
        if not self.supabase or not self.config.persist_decisions:
            return
        try:
            self.supabase.table("autonomous_decision_memory").upsert({"decision_id": memory.decision_id, "brand_id": memory.brand_id, "action": memory.action, "reason": memory.reason, "confidence": memory.confidence, "expected_value": memory.expected_value, "actual_value": memory.actual_value, "success": memory.success, "created_at": memory.created_at.isoformat(), "evaluated_at": memory.evaluated_at.isoformat() if memory.evaluated_at else None}).execute()
        except Exception as exc:
            logger.warning("Failed to persist decision memory: %s", exc)
    
    # ═══════════════════════════════════════════════════════════════════════
    # ADMIN
    # ═══════════════════════════════════════════════════════════════════════
    
    def clear_brand_cache(self, brand_id: str) -> None:
        """Clear brand cache."""
        with self._lock:
            self._observations.pop(brand_id, None)
            self._brand_health.pop(brand_id, None)
            self._states.pop(brand_id, None)
            for key in [k for k in self._baselines if k[0] == brand_id]:
                self._baselines.pop(key, None)
            for key in [k for k in self._trends if k[0] == brand_id]:
                self._trends.pop(key, None)
            for key in [k for k in self._topic_baselines if k[0] == brand_id]:
                self._topic_baselines.pop(key, None)
    
    def export_state(self) -> Dict[str, Any]:
        """Export full state."""
        return {"timestamp": iso_now(), "status": self.get_status(), "states": {bid: s.value for bid, s in self._states.items()}, "health": self._serialize(self._brand_health), "strategies": self._serialize(self._strategies), "goals": self._serialize(self._goals), "experiments": self._serialize(self._experiments), "learning": self.context_learner.get_learning_report()}


# ═══════════════════════════════════════════════════════════════════════════
# DEFAULT ACTION EXECUTOR
# ═══════════════════════════════════════════════════════════════════════════
