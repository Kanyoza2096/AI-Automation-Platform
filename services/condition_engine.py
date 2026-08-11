"""
KANYOZA AUTONOMOUS CONDITION + DECISION ENGINE v3.0
====================================================

Version: 3.0
Purpose:
    Autonomous decision intelligence for social-media/content systems.

v3.0 CHANGES:
    - Added set_action_executor() and set_supabase_client()
      so lifecycle.py can wire real services at boot time.
    - The autonomous loop is now CLOSED: Observe → Analyze → Decide → ACT.

Architecture:

    OBSERVE
       ↓
    NORMALIZE
       ↓
    BASELINE
       ↓
    TREND ANALYSIS
       ↓
    ANOMALY DETECTION
       ↓
    MULTI-SIGNAL EVALUATION
       ↓
    CONFIDENCE
       ↓
    GOAL EVALUATION
       ↓
    STRATEGY SELECTION
       ↓
    SAFETY / COOLDOWN CHECK
       ↓
    ACTION PLAN
       ↓
    EXECUTION / APPROVAL  ← set_action_executor() wires REAL handlers
       ↓
    OUTCOME EVALUATION
       ↓
    DECISION MEMORY
       ↓
    LEARNING

Designed to work with:
    - Supabase (via set_supabase_client)
    - ContentEngine (via set_action_executor)
    - Engagement tracker
    - Scheduler
    - Workflow system
    - Publisher
    - Analytics layer

No external AI model is required for the core decision process.
"""

from __future__ import annotations

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
from typing import (
    Any,
    Callable,
    Deque,
    Dict,
    Iterable,
    List,
    Optional,
    Sequence,
    Tuple,
)


# ============================================================================
# LOGGING
# ============================================================================

logger = logging.getLogger("kanyoza.condition_engine")


# ============================================================================
# TIME HELPERS
# ============================================================================

def utc_now() -> datetime:
    """Return timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def iso_now() -> str:
    """Return current UTC timestamp as ISO string."""
    return utc_now().isoformat()


def parse_datetime(value: Any) -> Optional[datetime]:
    """Safely parse common datetime representations."""
    if value is None:
        return None

    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    if isinstance(value, str):
        try:
            normalized = value.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(normalized)

            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)

            return parsed.astimezone(timezone.utc)

        except Exception:
            return None

    return None


# ============================================================================
# ENUMS
# ============================================================================

class DecisionState(str, Enum):
    OBSERVE = "observe"
    MONITOR = "monitor"
    BOOST = "boost"
    REDUCE = "reduce"
    EXPERIMENT = "experiment"
    RECOVER = "recover"
    PAUSE = "pause"
    CRITICAL = "critical"


class ActionRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ActionStatus(str, Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    EXECUTED = "executed"
    REJECTED = "rejected"
    FAILED = "failed"
    EXPIRED = "expired"


class GoalStatus(str, Enum):
    ACTIVE = "active"
    ACHIEVED = "achieved"
    AT_RISK = "at_risk"
    FAILED = "failed"
    PAUSED = "paused"


# ============================================================================
# CONFIGURATION
# ============================================================================

@dataclass
class EngineConfig:
    """Central configuration."""

    baseline_window: int = 30
    minimum_baseline_samples: int = 5
    minimum_confidence_for_action: float = 0.65
    strong_confidence: float = 0.85
    strong_performance_ratio: float = 1.50
    weak_performance_ratio: float = 0.70
    critical_performance_ratio: float = 0.45
    boost_enter_threshold: float = 1.50
    boost_exit_threshold: float = 1.10
    reduce_enter_threshold: float = 0.70
    reduce_exit_threshold: float = 0.95
    strong_positive_velocity: float = 0.15
    strong_negative_velocity: float = -0.15
    anomaly_z_score: float = 3.0
    minimum_anomaly_samples: int = 8
    default_action_cooldown_minutes: int = 360
    boost_cooldown_minutes: int = 720
    reduce_cooldown_minutes: int = 720
    experiment_cooldown_minutes: int = 1440
    allow_low_risk_autonomy: bool = True
    allow_medium_risk_autonomy: bool = True
    allow_high_risk_autonomy: bool = False
    allow_critical_autonomy: bool = False
    learning_rate: float = 0.20
    exploration_rate: float = 0.15
    goal_success_tolerance: float = 0.05
    dry_run: bool = False
    persist_decisions: bool = True


# ============================================================================
# DATA MODELS
# ============================================================================

@dataclass
class Observation:
    """A single observation of a post/content item."""

    post_id: str
    brand_id: str
    timestamp: datetime = field(default_factory=utc_now)
    likes: float = 0.0
    comments: float = 0.0
    shares: float = 0.0
    clicks: float = 0.0
    impressions: float = 0.0
    reach: float = 0.0
    engagement: float = 0.0
    engagement_rate: float = 0.0
    topic: str = ""
    content_type: str = ""
    style: str = ""
    hook_type: str = ""
    posting_hour: Optional[int] = None
    day_of_week: Optional[int] = None
    word_count: int = 0
    has_image: bool = False
    has_video: bool = False
    has_question: bool = False
    has_cta: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def normalize(self) -> "Observation":
        self.likes = max(0.0, float(self.likes))
        self.comments = max(0.0, float(self.comments))
        self.shares = max(0.0, float(self.shares))
        self.clicks = max(0.0, float(self.clicks))
        self.impressions = max(0.0, float(self.impressions))
        self.reach = max(0.0, float(self.reach))
        self.engagement = self.likes + self.comments + self.shares + self.clicks
        if self.impressions > 0:
            self.engagement_rate = (self.engagement / self.impressions) * 100
        if self.posting_hour is None:
            self.posting_hour = self.timestamp.hour
        if self.day_of_week is None:
            self.day_of_week = self.timestamp.weekday()
        return self


@dataclass
class Baseline:
    metric: str
    mean: float = 0.0
    median: float = 0.0
    stddev: float = 0.0
    minimum: float = 0.0
    maximum: float = 0.0
    sample_count: int = 0
    calculated_at: datetime = field(default_factory=utc_now)

    def ratio(self, value: float) -> float:
        if self.mean <= 0: return 1.0
        return value / self.mean

    def z_score(self, value: float) -> float:
        if self.stddev <= 0: return 0.0
        return (value - self.mean) / self.stddev


@dataclass
class Trend:
    metric: str
    velocity: float = 0.0
    acceleration: float = 0.0
    direction: str = "stable"
    confidence: float = 0.0
    sample_count: int = 0


@dataclass
class Anomaly:
    metric: str
    value: float
    expected: float
    z_score: float
    severity: str
    confidence: float
    detected_at: datetime = field(default_factory=utc_now)


@dataclass
class Goal:
    id: str
    brand_id: str
    name: str
    metric: str
    target_value: float
    baseline_value: float
    deadline: Optional[datetime] = None
    status: GoalStatus = GoalStatus.ACTIVE
    created_at: datetime = field(default_factory=utc_now)
    progress: float = 0.0

    def calculate_progress(self, current_value: float) -> float:
        delta = self.target_value - self.baseline_value
        if delta == 0:
            self.progress = 1.0
            return self.progress
        self.progress = (current_value - self.baseline_value) / delta
        self.progress = max(0.0, min(1.0, self.progress))
        return self.progress


@dataclass
class Strategy:
    id: str
    name: str
    description: str = ""
    reward: float = 0.0
    trials: int = 0
    successes: int = 0
    failures: int = 0
    enabled: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def success_rate(self) -> float:
        if self.trials <= 0: return 0.5
        return self.successes / self.trials


@dataclass
class Decision:
    id: str
    brand_id: str
    state: DecisionState
    confidence: float
    reason: str
    evidence: Dict[str, Any]
    recommended_action: Optional[str] = None
    risk: ActionRisk = ActionRisk.LOW
    requires_approval: bool = False
    created_at: datetime = field(default_factory=utc_now)
    expires_at: Optional[datetime] = None
    status: ActionStatus = ActionStatus.PROPOSED


@dataclass
class ActionPlan:
    id: str
    decision_id: str
    brand_id: str
    action: str
    risk: ActionRisk
    confidence: float
    parameters: Dict[str, Any] = field(default_factory=dict)
    expected_outcome: Dict[str, Any] = field(default_factory=dict)
    status: ActionStatus = ActionStatus.PROPOSED
    created_at: datetime = field(default_factory=utc_now)
    executed_at: Optional[datetime] = None
    outcome: Optional[Dict[str, Any]] = None


@dataclass
class Experiment:
    id: str
    brand_id: str
    name: str
    variable: str
    control: str
    variant: str
    status: str = "running"
    control_results: List[float] = field(default_factory=list)
    variant_results: List[float] = field(default_factory=list)
    created_at: datetime = field(default_factory=utc_now)
    minimum_samples: int = 5
    winner: Optional[str] = None


@dataclass
class DecisionMemory:
    decision_id: str
    brand_id: str
    action: str
    reason: str
    confidence: float
    expected_value: float
    actual_value: Optional[float] = None
    success: Optional[bool] = None
    created_at: datetime = field(default_factory=utc_now)
    evaluated_at: Optional[datetime] = None


# ============================================================================
# UTILITIES
# ============================================================================

def safe_mean(values: Sequence[float]) -> float:
    if not values: return 0.0
    return statistics.mean(values)


def safe_median(values: Sequence[float]) -> float:
    if not values: return 0.0
    return statistics.median(values)


def safe_stddev(values: Sequence[float]) -> float:
    if len(values) < 2: return 0.0
    return statistics.stdev(values)


def clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))


def generate_id(prefix: str) -> str:
    raw = f"{prefix}:{time.time_ns()}:{random.random()}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


# ============================================================================
# AUTONOMOUS CONDITION ENGINE v3.0
# ============================================================================

class AutonomousConditionEngine:
    """Autonomous condition + decision engine.

    v3.0: Added set_action_executor() and set_supabase_client()
    so lifecycle.py can wire real services at boot time.
    """

    def __init__(
        self,
        supabase_client=None,
        config: Optional[EngineConfig] = None,
        action_executor: Optional[Callable[[ActionPlan], Dict[str, Any]]] = None,
    ):
        self.supabase = supabase_client
        self.config = config or EngineConfig()
        self.action_executor = action_executor
        self._lock = threading.RLock()
        self._observations: Dict[str, Deque[Observation]] = defaultdict(lambda: deque(maxlen=500))
        self._baselines: Dict[Tuple[str, str], Baseline] = {}
        self._trends: Dict[Tuple[str, str], Trend] = {}
        self._brand_health: Dict[str, Dict[str, Any]] = {}
        self._decisions: Dict[str, Decision] = {}
        self._plans: Dict[str, ActionPlan] = {}
        self._decision_memory: List[DecisionMemory] = []
        self._goals: Dict[str, Goal] = {}
        self._strategies: Dict[str, Strategy] = {}
        self._experiments: Dict[str, Experiment] = {}
        self._last_action_at: Dict[Tuple[str, str], datetime] = {}
        self._states: Dict[str, DecisionState] = {}
        self._register_default_strategies()
        logger.info("Autonomous Condition Engine v3.0 initialized")

    # ═══════════════════════════════════════════════════════════════
    # v3.0 — WIRING METHODS (called by lifecycle.py)
    # ═══════════════════════════════════════════════════════════════

    def set_action_executor(
        self,
        executor: Callable[[ActionPlan], Dict[str, Any]],
    ) -> None:
        """Replace the action executor after initialization.

        Called by lifecycle.py during boot to wire the real
        ContentEngine, EventBus, and NotificationService.
        """
        self.action_executor = executor
        logger.info("ConditionEngine: action executor updated")

    def set_supabase_client(self, client) -> None:
        """Set or update the Supabase client after initialization.

        Called by lifecycle.py to enable decision persistence.
        """
        self.supabase = client
        logger.info("ConditionEngine: Supabase client updated")

    # ==================================================================
    # STRATEGIES
    # ==================================================================

    def _register_default_strategies(self):
        defaults = [
            Strategy(id="technical_tutorial", name="Technical Tutorials", description="Practical technical how-to content."),
            Strategy(id="technical_story", name="Technical Stories", description="Personal engineering experiences."),
            Strategy(id="technical_opinion", name="Technical Opinions", description="Strong engineering opinions."),
            Strategy(id="industry_news", name="Industry News", description="Relevant technology news."),
            Strategy(id="educational_tip", name="Educational Tips", description="Short practical technical lessons."),
        ]
        for strategy in defaults:
            self._strategies[strategy.id] = strategy

    # ==================================================================
    # OBSERVATION
    # ==================================================================

    def observe(self, observation: Observation) -> Dict[str, Any]:
        observation.normalize()
        with self._lock:
            history = self._observations[observation.brand_id]
            history.append(observation)
            baseline = self._calculate_baseline(observation.brand_id, "engagement")
            trend = self._calculate_trend(observation.brand_id, "engagement")
            anomaly = self._detect_anomaly(observation, baseline)
            ratio = baseline.ratio(observation.engagement)
            state = self._determine_state(observation.brand_id, ratio, trend, anomaly)
            confidence = self._calculate_confidence(observation=observation, baseline=baseline, trend=trend, anomaly=anomaly)
            decision = self._make_decision(observation=observation, baseline=baseline, trend=trend, anomaly=anomaly, state=state, confidence=confidence)
            self._decisions[decision.id] = decision
            self._persist_decision(decision)
            return {"observation": self._serialize(observation), "baseline": self._serialize(baseline), "trend": self._serialize(trend), "anomaly": self._serialize(anomaly), "state": state.value, "confidence": confidence, "decision": self._serialize(decision)}

    # ==================================================================
    # BASELINE
    # ==================================================================

    def _calculate_baseline(self, brand_id: str, metric: str) -> Baseline:
        key = (brand_id, metric)
        history = list(self._observations.get(brand_id, []))
        values = [self._metric_value(item, metric) for item in history]
        values = [float(v) for v in values if v is not None]
        values = values[-self.config.baseline_window:]
        if not values:
            baseline = Baseline(metric=metric)
        else:
            baseline = Baseline(metric=metric, mean=round(safe_mean(values), 4), median=round(safe_median(values), 4), stddev=round(safe_stddev(values), 4), minimum=min(values), maximum=max(values), sample_count=len(values))
        self._baselines[key] = baseline
        return baseline

    # ==================================================================
    # TREND ANALYSIS
    # ==================================================================

    def _calculate_trend(self, brand_id: str, metric: str) -> Trend:
        history = list(self._observations.get(brand_id, []))
        values = [self._metric_value(item, metric) for item in history]
        values = [float(v) for v in values if v is not None]
        if len(values) < 3:
            trend = Trend(metric=metric, confidence=0.1, sample_count=len(values))
            self._trends[(brand_id, metric)] = trend
            return trend
        recent = values[-5:]
        if len(recent) < 2: velocity = 0.0
        else:
            first, last = recent[0], recent[-1]
            velocity = 0.0 if first == 0 else (last - first) / abs(first)
        midpoint = len(recent) // 2
        first_half, second_half = recent[:midpoint], recent[midpoint:]
        first_mean, second_mean = safe_mean(first_half), safe_mean(second_half)
        acceleration = 0.0 if first_mean == 0 else (second_mean - first_mean) / abs(first_mean)
        if velocity >= self.config.strong_positive_velocity: direction = "rising"
        elif velocity <= self.config.strong_negative_velocity: direction = "falling"
        else: direction = "stable"
        confidence = clamp(len(values) / self.config.baseline_window)
        trend = Trend(metric=metric, velocity=round(velocity, 4), acceleration=round(acceleration, 4), direction=direction, confidence=round(confidence, 4), sample_count=len(values))
        self._trends[(brand_id, metric)] = trend
        return trend

    # ==================================================================
    # ANOMALY DETECTION
    # ==================================================================

    def _detect_anomaly(self, observation: Observation, baseline: Baseline) -> Optional[Anomaly]:
        if baseline.sample_count < self.config.minimum_anomaly_samples: return None
        z = baseline.z_score(observation.engagement)
        if abs(z) < self.config.anomaly_z_score: return None
        severity = "positive" if z >= 0 else "negative"
        confidence = clamp(abs(z) / 6.0)
        return Anomaly(metric="engagement", value=observation.engagement, expected=baseline.mean, z_score=round(z, 4), severity=severity, confidence=round(confidence, 4))

    # ==================================================================
    # MULTI-SIGNAL CONFIDENCE
    # ==================================================================

    def _calculate_confidence(self, observation: Observation, baseline: Baseline, trend: Trend, anomaly: Optional[Anomaly]) -> float:
        components = []
        components.append(clamp(baseline.sample_count / 30))
        if baseline.mean > 0: stability = clamp(1.0 - (baseline.stddev / baseline.mean))
        else: stability = 0.2
        components.append(stability)
        components.append(trend.confidence)
        components.append(anomaly.confidence if anomaly else 0.5)
        checks = [bool(observation.topic), bool(observation.content_type), bool(observation.style), observation.posting_hour is not None]
        metadata_score = sum(checks) / len(checks) if checks else 0.0
        components.append(metadata_score)
        return round(safe_mean(components), 4)

    # ==================================================================
    # STATE MACHINE
    # ==================================================================

    def _determine_state(self, brand_id: str, ratio: float, trend: Trend, anomaly: Optional[Anomaly]) -> DecisionState:
        current = self._states.get(brand_id, DecisionState.OBSERVE)
        if ratio <= self.config.critical_performance_ratio: new_state = DecisionState.CRITICAL
        elif ratio >= self.config.boost_enter_threshold: new_state = DecisionState.BOOST
        elif current == DecisionState.BOOST and ratio >= self.config.boost_exit_threshold: new_state = DecisionState.BOOST
        elif ratio <= self.config.reduce_enter_threshold: new_state = DecisionState.REDUCE
        elif current == DecisionState.REDUCE and ratio <= self.config.reduce_exit_threshold: new_state = DecisionState.REDUCE
        elif trend.direction == "rising": new_state = DecisionState.MONITOR
        elif trend.direction == "falling": new_state = DecisionState.MONITOR
        else: new_state = DecisionState.OBSERVE
        self._states[brand_id] = new_state
        return new_state

    # ==================================================================
    # DECISION ENGINE
    # ==================================================================

    def _make_decision(self, observation: Observation, baseline: Baseline, trend: Trend, anomaly: Optional[Anomaly], state: DecisionState, confidence: float) -> Decision:
        ratio = baseline.ratio(observation.engagement)
        evidence = {"engagement": observation.engagement, "baseline": baseline.mean, "engagement_ratio": round(ratio, 4), "trend": {"direction": trend.direction, "velocity": trend.velocity, "acceleration": trend.acceleration}, "sample_count": baseline.sample_count}
        if anomaly: evidence["anomaly"] = {"z_score": anomaly.z_score, "severity": anomaly.severity, "confidence": anomaly.confidence}
        if state == DecisionState.CRITICAL: action, reason, risk = "reduce_content_risk", "Performance is critically below the historical baseline.", ActionRisk.MEDIUM
        elif state == DecisionState.BOOST: action, reason, risk = "increase_similar_content", "Current performance significantly exceeds the historical baseline.", ActionRisk.LOW
        elif state == DecisionState.REDUCE: action, reason, risk = "reduce_underperforming_content", "Current performance is materially below the historical baseline.", ActionRisk.MEDIUM
        elif trend.direction == "rising": action, reason, risk = "monitor_positive_trend", "Performance is showing positive momentum.", ActionRisk.LOW
        elif trend.direction == "falling": action, reason, risk = "investigate_decline", "Performance is showing negative momentum.", ActionRisk.LOW
        else: action, reason, risk = None, "Evidence is insufficient for a meaningful autonomous intervention.", ActionRisk.LOW
        requires_approval = risk in {ActionRisk.HIGH, ActionRisk.CRITICAL} or confidence < self.config.minimum_confidence_for_action
        return Decision(id=generate_id("decision"), brand_id=observation.brand_id, state=state, confidence=confidence, reason=reason, evidence=evidence, recommended_action=action, risk=risk, requires_approval=requires_approval)

    # ==================================================================
    # ACTION PLANNING
    # ==================================================================

    def create_action_plan(self, decision: Decision) -> Optional[ActionPlan]:
        if not decision.recommended_action: return None
        action = decision.recommended_action
        if self._is_on_cooldown(decision.brand_id, action): return None
        if not self._is_action_allowed(decision.risk, decision.confidence): return None
        parameters = self._build_action_parameters(decision)
        expected = self._estimate_expected_outcome(decision)
        plan = ActionPlan(id=generate_id("plan"), decision_id=decision.id, brand_id=decision.brand_id, action=action, risk=decision.risk, confidence=decision.confidence, parameters=parameters, expected_outcome=expected)
        self._plans[plan.id] = plan
        return plan

    def _build_action_parameters(self, decision: Decision) -> Dict[str, Any]:
        action = decision.recommended_action
        if action == "increase_similar_content": return {"content_mix_change": 0.20, "preserve_topic": True, "preserve_style": True, "duration_posts": 5}
        if action == "reduce_underperforming_content": return {"content_mix_change": -0.20, "duration_posts": 5}
        if action == "reduce_content_risk": return {"content_mix_change": -0.30, "experimental_content": False, "duration_posts": 5}
        if action == "investigate_decline": return {"run_experiment": True, "collect_more_data": True}
        if action == "monitor_positive_trend": return {"increase_observation_frequency": True}
        return {}

    def _estimate_expected_outcome(self, decision: Decision) -> Dict[str, Any]:
        baseline = decision.evidence.get("baseline", 0)
        ratio = decision.evidence.get("engagement_ratio", 1.0)
        action = decision.recommended_action
        if action == "increase_similar_content": target = baseline * 1.10
        elif action == "reduce_underperforming_content": target = baseline * 0.95
        elif action == "reduce_content_risk": target = baseline * 0.85
        else: target = baseline
        return {"metric": "engagement", "baseline": baseline, "current_ratio": ratio, "target": round(target, 2)}

    # ==================================================================
    # SAFETY & COOLDOWNS
    # ==================================================================

    def _is_action_allowed(self, risk: ActionRisk, confidence: float) -> bool:
        if confidence < self.config.minimum_confidence_for_action: return False
        if risk == ActionRisk.LOW: return self.config.allow_low_risk_autonomy
        if risk == ActionRisk.MEDIUM: return self.config.allow_medium_risk_autonomy
        if risk == ActionRisk.HIGH: return self.config.allow_high_risk_autonomy
        if risk == ActionRisk.CRITICAL: return self.config.allow_critical_autonomy
        return False

    def _is_on_cooldown(self, brand_id: str, action: str) -> bool:
        key = (brand_id, action)
        last = self._last_action_at.get(key)
        if not last: return False
        if action.startswith("increase"): minutes = self.config.boost_cooldown_minutes
        elif action.startswith("reduce"): minutes = self.config.reduce_cooldown_minutes
        else: minutes = self.config.default_action_cooldown_minutes
        return (utc_now() - last) < timedelta(minutes=minutes)

    # ==================================================================
    # EXECUTION
    # ==================================================================

    def execute_plan(self, plan_id: str, approved: bool = False) -> ActionPlan:
        plan = self._plans.get(plan_id)
        if not plan: raise ValueError(f"Unknown action plan: {plan_id}")
        if plan.risk in {ActionRisk.HIGH, ActionRisk.CRITICAL} and not approved:
            plan.status = ActionStatus.REJECTED; return plan
        if self.config.dry_run:
            plan.status = ActionStatus.APPROVED; return plan
        if not self.action_executor:
            plan.status = ActionStatus.APPROVED; return plan
        try:
            result = self.action_executor(plan)
            plan.outcome = result; plan.status = ActionStatus.EXECUTED; plan.executed_at = utc_now()
            self._last_action_at[(plan.brand_id, plan.action)] = utc_now()
            return plan
        except Exception as exc:
            plan.status = ActionStatus.FAILED; plan.outcome = {"error": str(exc)}
            return plan

    # ==================================================================
    # GOALS
    # ==================================================================

    def register_goal(self, goal: Goal) -> Goal:
        self._goals[goal.id] = goal
        return goal

    def evaluate_goals(self, brand_id: str, metrics: Dict[str, float]) -> List[Dict[str, Any]]:
        results = []
        for goal in self._goals.values():
            if goal.brand_id != brand_id or goal.status not in {GoalStatus.ACTIVE, GoalStatus.AT_RISK}: continue
            current = metrics.get(goal.metric)
            if current is None: continue
            progress = goal.calculate_progress(current)
            if progress >= 1.0: goal.status = GoalStatus.ACHIEVED
            elif progress < 0.25: goal.status = GoalStatus.AT_RISK
            results.append({"goal_id": goal.id, "name": goal.name, "metric": goal.metric, "current": current, "target": goal.target_value, "progress": progress, "status": goal.status.value})
        return results

    # ==================================================================
    # BRAND HEALTH
    # ==================================================================

    def calculate_brand_health(self, brand_id: str) -> Dict[str, Any]:
        observations = list(self._observations.get(brand_id, []))
        if not observations:
            health = {"score": 50.0, "status": "unknown", "components": {}}
            self._brand_health[brand_id] = health
            return health
        baseline = self._calculate_baseline(brand_id, "engagement")
        trend = self._calculate_trend(brand_id, "engagement")
        recent = observations[-10:]
        recent_average = safe_mean([item.engagement for item in recent])
        performance_score = clamp(recent_average / baseline.mean / 2) * 100 if baseline.mean > 0 else 50
        trend_score = 85 if trend.direction == "rising" else 25 if trend.direction == "falling" else 50
        consistency_values = [item.engagement for item in recent]
        consistency = 50
        if consistency_values:
            mean_val = safe_mean(consistency_values); std_val = safe_stddev(consistency_values)
            if mean_val > 0: consistency = clamp(1 - (std_val / mean_val)) * 100
        score = round(clamp(performance_score * 0.50 + trend_score * 0.30 + consistency * 0.20, 0, 100), 2)
        status = "healthy" if score >= 80 else "stable" if score >= 60 else "watch" if score >= 40 else "weak" if score >= 20 else "critical"
        health = {"score": score, "status": status, "components": {"performance": round(performance_score, 2), "trend": round(trend_score, 2), "consistency": round(consistency, 2)}, "updated_at": iso_now()}
        self._brand_health[brand_id] = health
        return health

    # ==================================================================
    # STRATEGY SELECTION
    # ==================================================================

    def select_strategy(self, brand_id: str) -> Optional[Strategy]:
        strategies = [s for s in self._strategies.values() if s.enabled]
        if not strategies: return None
        if random.random() < self.config.exploration_rate: return random.choice(strategies)
        return max(strategies, key=lambda s: s.reward + s.success_rate)

    def record_strategy_result(self, strategy_id: str, reward: float, success: bool) -> None:
        strategy = self._strategies.get(strategy_id)
        if not strategy: return
        strategy.trials += 1
        if success: strategy.successes += 1
        else: strategy.failures += 1
        strategy.reward = (1 - self.config.learning_rate) * strategy.reward + self.config.learning_rate * reward

    # ==================================================================
    # EXPERIMENTS
    # ==================================================================

    def create_experiment(self, brand_id: str, name: str, variable: str, control: str, variant: str) -> Experiment:
        experiment = Experiment(id=generate_id("experiment"), brand_id=brand_id, name=name, variable=variable, control=control, variant=variant)
        self._experiments[experiment.id] = experiment
        return experiment

    def record_experiment_result(self, experiment_id: str, variant: str, result: float) -> Optional[Dict[str, Any]]:
        experiment = self._experiments.get(experiment_id)
        if not experiment: return None
        if variant == "control": experiment.control_results.append(float(result))
        elif variant == "variant": experiment.variant_results.append(float(result))
        else: raise ValueError("variant must be 'control' or 'variant'")
        return self.evaluate_experiment(experiment_id)

    def evaluate_experiment(self, experiment_id: str) -> Dict[str, Any]:
        experiment = self._experiments.get(experiment_id)
        if not experiment: raise ValueError(f"Unknown experiment: {experiment_id}")
        control, variant = experiment.control_results, experiment.variant_results
        if len(control) < experiment.minimum_samples or len(variant) < experiment.minimum_samples:
            return {"status": "insufficient_data", "control_samples": len(control), "variant_samples": len(variant)}
        control_mean, variant_mean = safe_mean(control), safe_mean(variant)
        improvement = 0.0 if control_mean == 0 else (variant_mean - control_mean) / abs(control_mean)
        winner = "variant" if improvement > 0.05 else "control" if improvement < -0.05 else "inconclusive"
        experiment.winner = winner
        if winner != "inconclusive": experiment.status = "completed"
        return {"status": experiment.status, "winner": winner, "control_mean": control_mean, "variant_mean": variant_mean, "improvement": round(improvement, 4), "control_samples": len(control), "variant_samples": len(variant)}

    # ==================================================================
    # DECISION OUTCOME & LEARNING
    # ==================================================================

    def record_decision_outcome(self, decision_id: str, actual_value: float) -> Optional[DecisionMemory]:
        decision = self._decisions.get(decision_id)
        if not decision: return None
        expected = decision.evidence.get("baseline", 0)
        action = decision.recommended_action
        if action == "increase_similar_content": success = actual_value >= expected * 1.10
        elif action in {"reduce_underperforming_content", "reduce_content_risk"}: success = actual_value >= expected * 0.95
        else: success = actual_value >= expected
        memory = DecisionMemory(decision_id=decision_id, brand_id=decision.brand_id, action=action or "observe", reason=decision.reason, confidence=decision.confidence, expected_value=expected, actual_value=actual_value, success=success, evaluated_at=utc_now())
        self._decision_memory.append(memory)
        self._learn_from_decision(memory)
        self._persist_decision_memory(memory)
        return memory

    def _learn_from_decision(self, memory: DecisionMemory) -> None:
        if memory.actual_value is None or memory.expected_value <= 0: return
        reward = clamp(memory.actual_value / memory.expected_value, 0, 2)
        strategy_map = {"increase_similar_content": "technical_tutorial", "reduce_underperforming_content": "technical_tutorial"}
        strategy_id = strategy_map.get(memory.action)
        if strategy_id: self.record_strategy_result(strategy_id=strategy_id, reward=reward, success=bool(memory.success))

    # ==================================================================
    # AUTONOMOUS CYCLE
    # ==================================================================

    def run_cycle(self, observations: Iterable[Observation]) -> Dict[str, Any]:
        results = []; grouped: Dict[str, List[Observation]] = defaultdict(list)
        for observation in observations:
            grouped[observation.brand_id].append(observation); results.append(self.observe(observation))
        brand_reports = {}
        for brand_id in grouped:
            health = self.calculate_brand_health(brand_id)
            goals = self.evaluate_goals(brand_id, {"engagement": safe_mean([item.engagement for item in grouped[brand_id]])})
            strategy = self.select_strategy(brand_id)
            brand_reports[brand_id] = {"health": health, "goals": goals, "recommended_strategy": self._serialize(strategy) if strategy else None}
        return {"timestamp": iso_now(), "observations_processed": len(results), "results": results, "brands": brand_reports}

    # ==================================================================
    # STATUS & SERIALIZATION
    # ==================================================================

    def explain_decision(self, decision_id: str) -> Dict[str, Any]:
        decision = self._decisions.get(decision_id)
        if not decision: return {"error": "Decision not found"}
        return {"decision_id": decision.id, "brand_id": decision.brand_id, "state": decision.state.value, "confidence": decision.confidence, "reason": decision.reason, "recommended_action": decision.recommended_action, "risk": decision.risk.value, "requires_approval": decision.requires_approval, "evidence": decision.evidence, "created_at": decision.created_at.isoformat()}

    def get_status(self, brand_id: Optional[str] = None) -> Dict[str, Any]:
        if brand_id:
            observations = list(self._observations.get(brand_id, []))
            state = self._states.get(brand_id, DecisionState.OBSERVE)
            health = self._brand_health.get(brand_id)
            return {"brand_id": brand_id, "state": state.value, "health": health, "observation_count": len(observations), "active_goals": len([g for g in self._goals.values() if g.brand_id == brand_id and g.status == GoalStatus.ACTIVE]), "active_experiments": len([e for e in self._experiments.values() if e.brand_id == brand_id and e.status == "running"])}
        return {"brands": len(self._observations), "observations": sum(len(items) for items in self._observations.values()), "decisions": len(self._decisions), "plans": len(self._plans), "goals": len(self._goals), "experiments": len(self._experiments), "strategies": len(self._strategies), "decision_memory": len(self._decision_memory), "dry_run": self.config.dry_run, "timestamp": iso_now()}

    @staticmethod
    def _metric_value(observation: Observation, metric: str) -> float:
        try: return float(getattr(observation, metric, 0.0))
        except: return 0.0

    @staticmethod
    def _serialize(value: Any) -> Any:
        if value is None: return None
        if isinstance(value, Enum): return value.value
        if isinstance(value, datetime): return value.isoformat()
        if hasattr(value, "__dataclass_fields__"): return {key: AutonomousConditionEngine._serialize(item) for key, item in asdict(value).items()}
        if isinstance(value, dict): return {str(k): AutonomousConditionEngine._serialize(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)): return [AutonomousConditionEngine._serialize(item) for item in value]
        return value

    # ==================================================================
    # PERSISTENCE
    # ==================================================================

    def _persist_decision(self, decision: Decision) -> None:
        if not self.supabase or not self.config.persist_decisions: return
        try:
            self.supabase.table("autonomous_decisions").upsert({"id": decision.id, "brand_id": decision.brand_id, "state": decision.state.value, "confidence": decision.confidence, "reason": decision.reason, "recommended_action": decision.recommended_action, "risk": decision.risk.value, "requires_approval": decision.requires_approval, "evidence": decision.evidence, "created_at": decision.created_at.isoformat()}).execute()
        except Exception as exc: logger.warning("Failed to persist decision: %s", exc)

    def _persist_decision_memory(self, memory: DecisionMemory) -> None:
        if not self.supabase or not self.config.persist_decisions: return
        try:
            self.supabase.table("autonomous_decision_memory").upsert({"decision_id": memory.decision_id, "brand_id": memory.brand_id, "action": memory.action, "reason": memory.reason, "confidence": memory.confidence, "expected_value": memory.expected_value, "actual_value": memory.actual_value, "success": memory.success, "created_at": memory.created_at.isoformat(), "evaluated_at": memory.evaluated_at.isoformat() if memory.evaluated_at else None}).execute()
        except Exception as exc: logger.warning("Failed to persist decision memory: %s", exc)

    # ==================================================================
    # ADMIN
    # ==================================================================

    def clear_brand_cache(self, brand_id: str) -> None:
        with self._lock:
            self._observations.pop(brand_id, None); self._brand_health.pop(brand_id, None); self._states.pop(brand_id, None)
            for key in [k for k in self._baselines if k[0] == brand_id]: self._baselines.pop(key, None)
            for key in [k for k in self._trends if k[0] == brand_id]: self._trends.pop(key, None)

    def export_state(self) -> Dict[str, Any]:
        return {"timestamp": iso_now(), "status": self.get_status(), "states": {bid: s.value for bid, s in self._states.items()}, "health": self._serialize(self._brand_health), "strategies": self._serialize(self._strategies), "goals": self._serialize(self._goals), "experiments": self._serialize(self._experiments)}


# ============================================================================
# DEFAULT ACTION EXECUTOR (placeholder — replaced by lifecycle.py)
# ============================================================================

def default_action_executor(plan: ActionPlan) -> Dict[str, Any]:
    logger.info("Action executor received: %s", plan.action)
    return {"success": True, "action": plan.action, "parameters": plan.parameters, "executed_at": iso_now(), "message": "Action accepted by autonomous executor."}


# ============================================================================
# SINGLETON
# ============================================================================

autonomous_engine = AutonomousConditionEngine(
    config=EngineConfig(dry_run=False, allow_low_risk_autonomy=True, allow_medium_risk_autonomy=True, allow_high_risk_autonomy=False, allow_critical_autonomy=False),
    action_executor=default_action_executor,
)


# ============================================================================
# CONVENIENCE API
# ============================================================================

def evaluate_engagement(brand_id: str, post_id: str, likes: int = 0, comments: int = 0, shares: int = 0, clicks: int = 0, impressions: int = 0, reach: int = 0, topic: str = "", content_type: str = "", style: str = "", hook_type: str = "", metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    observation = Observation(brand_id=brand_id, post_id=post_id, likes=likes, comments=comments, shares=shares, clicks=clicks, impressions=impressions, reach=reach, topic=topic, content_type=content_type, style=style, hook_type=hook_type, metadata=metadata or {})
    return autonomous_engine.observe(observation)


def get_engine_status(brand_id: Optional[str] = None) -> Dict[str, Any]:
    return autonomous_engine.get_status(brand_id)


def register_default_goal(brand_id: str) -> Goal:
    return autonomous_engine.register_goal(Goal(id=generate_id("goal"), brand_id=brand_id, name="Increase meaningful engagement", metric="engagement", target_value=120.0, baseline_value=100.0, deadline=(utc_now() + timedelta(days=30))))


def create_hook_experiment(brand_id: str) -> Experiment:
    return autonomous_engine.create_experiment(brand_id=brand_id, name="Hook Performance Experiment", variable="hook_type", control="direct_technical_hook", variant="story_based_hook")


def health_check() -> Dict[str, Any]:
    return {"service": "autonomous_condition_engine", "status": "healthy", "version": "3.0", "timestamp": iso_now(), "engine": autonomous_engine.get_status()}


# ============================================================================
# MODULE EXPORTS
# ============================================================================

__all__ = [
    "EngineConfig", "Observation", "Baseline", "Trend", "Anomaly", "Goal", "Strategy", "Decision", "ActionPlan", "Experiment", "DecisionMemory",
    "DecisionState", "ActionRisk", "ActionStatus", "GoalStatus",
    "AutonomousConditionEngine", "autonomous_engine",
    "evaluate_engagement", "get_engine_status", "register_default_goal", "create_hook_experiment", "health_check",
]
