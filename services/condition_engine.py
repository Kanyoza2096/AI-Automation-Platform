"""
KANYOZA AUTONOMOUS CONDITION + DECISION ENGINE
================================================

Version: 2.0
Purpose:
    Autonomous decision intelligence for social-media/content systems.

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
    EXECUTION / APPROVAL
       ↓
    OUTCOME EVALUATION
       ↓
    DECISION MEMORY
       ↓
    LEARNING

The engine is intentionally contained in ONE FILE for the first version.
It can later be split into specialized modules.

Designed to work with:
    - Supabase
    - existing engagement tracker
    - content engine
    - scheduler
    - workflow system
    - publisher
    - analytics layer

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
    """
    Central configuration.

    Keeping these values together makes the engine much easier to tune
    without modifying decision logic.
    """

    # ----------------------------------------------------------------------
    # Baseline
    # ----------------------------------------------------------------------

    baseline_window: int = 30

    minimum_baseline_samples: int = 5

    # ----------------------------------------------------------------------
    # Confidence
    # ----------------------------------------------------------------------

    minimum_confidence_for_action: float = 0.65

    strong_confidence: float = 0.85

    # ----------------------------------------------------------------------
    # Engagement thresholds
    # ----------------------------------------------------------------------

    strong_performance_ratio: float = 1.50

    weak_performance_ratio: float = 0.70

    critical_performance_ratio: float = 0.45

    # ----------------------------------------------------------------------
    # Hysteresis
    # ----------------------------------------------------------------------

    boost_enter_threshold: float = 1.50

    boost_exit_threshold: float = 1.10

    reduce_enter_threshold: float = 0.70

    reduce_exit_threshold: float = 0.95

    # ----------------------------------------------------------------------
    # Trend
    # ----------------------------------------------------------------------

    strong_positive_velocity: float = 0.15

    strong_negative_velocity: float = -0.15

    # ----------------------------------------------------------------------
    # Anomaly detection
    # ----------------------------------------------------------------------

    anomaly_z_score: float = 3.0

    minimum_anomaly_samples: int = 8

    # ----------------------------------------------------------------------
    # Cooldowns
    # ----------------------------------------------------------------------

    default_action_cooldown_minutes: int = 360

    boost_cooldown_minutes: int = 720

    reduce_cooldown_minutes: int = 720

    experiment_cooldown_minutes: int = 1440

    # ----------------------------------------------------------------------
    # Safety
    # ----------------------------------------------------------------------

    allow_low_risk_autonomy: bool = True

    allow_medium_risk_autonomy: bool = True

    allow_high_risk_autonomy: bool = False

    allow_critical_autonomy: bool = False

    # ----------------------------------------------------------------------
    # Learning
    # ----------------------------------------------------------------------

    learning_rate: float = 0.20

    exploration_rate: float = 0.15

    # ----------------------------------------------------------------------
    # Goal
    # ----------------------------------------------------------------------

    goal_success_tolerance: float = 0.05

    # ----------------------------------------------------------------------
    # Runtime
    # ----------------------------------------------------------------------

    dry_run: bool = False

    persist_decisions: bool = True


# ============================================================================
# DATA MODELS
# ============================================================================

@dataclass
class Observation:
    """
    A single observation of a post/content item.
    """

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

    # Content metadata
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
        """
        Normalize and calculate derived values.
        """

        self.likes = max(0.0, float(self.likes))
        self.comments = max(0.0, float(self.comments))
        self.shares = max(0.0, float(self.shares))
        self.clicks = max(0.0, float(self.clicks))

        self.impressions = max(0.0, float(self.impressions))
        self.reach = max(0.0, float(self.reach))

        self.engagement = (
            self.likes
            + self.comments
            + self.shares
            + self.clicks
        )

        if self.impressions > 0:
            self.engagement_rate = (
                self.engagement / self.impressions
            ) * 100

        if self.posting_hour is None:
            self.posting_hour = self.timestamp.hour

        if self.day_of_week is None:
            self.day_of_week = self.timestamp.weekday()

        return self


@dataclass
class Baseline:
    """Historical performance baseline."""

    metric: str

    mean: float = 0.0

    median: float = 0.0

    stddev: float = 0.0

    minimum: float = 0.0

    maximum: float = 0.0

    sample_count: int = 0

    calculated_at: datetime = field(default_factory=utc_now)

    def ratio(self, value: float) -> float:
        if self.mean <= 0:
            return 1.0

        return value / self.mean

    def z_score(self, value: float) -> float:
        if self.stddev <= 0:
            return 0.0

        return (value - self.mean) / self.stddev


@dataclass
class Trend:
    """Temporal performance analysis."""

    metric: str

    velocity: float = 0.0

    acceleration: float = 0.0

    direction: str = "stable"

    confidence: float = 0.0

    sample_count: int = 0


@dataclass
class Anomaly:
    """Detected statistical anomaly."""

    metric: str

    value: float

    expected: float

    z_score: float

    severity: str

    confidence: float

    detected_at: datetime = field(default_factory=utc_now)


@dataclass
class Goal:
    """Autonomous optimization goal."""

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

        self.progress = (
            (current_value - self.baseline_value)
            / delta
        )

        self.progress = max(0.0, min(1.0, self.progress))

        return self.progress


@dataclass
class Strategy:
    """A content strategy candidate."""

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
        if self.trials <= 0:
            return 0.5

        return self.successes / self.trials


@dataclass
class Decision:
    """Complete explainable autonomous decision."""

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
    """Concrete execution plan."""

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
    """Controlled experiment."""

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
    """Historical decision/outcome record."""

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
    if not values:
        return 0.0

    return statistics.mean(values)


def safe_median(values: Sequence[float]) -> float:
    if not values:
        return 0.0

    return statistics.median(values)


def safe_stddev(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0

    return statistics.stdev(values)


def clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))


def generate_id(prefix: str) -> str:
    raw = f"{prefix}:{time.time_ns()}:{random.random()}"

    digest = hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()[:16]

    return f"{prefix}_{digest}"


# ============================================================================
# AUTONOMOUS CONDITION ENGINE
# ============================================================================

class AutonomousConditionEngine:
    """
    Autonomous condition + decision engine.

    The engine deliberately separates:

        Evidence
        ↓
        Analysis
        ↓
        Decision
        ↓
        Action
        ↓
        Outcome

    This prevents the system from blindly executing rules.
    """

    def __init__(
        self,
        supabase_client=None,
        config: Optional[EngineConfig] = None,
        action_executor: Optional[
            Callable[[ActionPlan], Dict[str, Any]]
        ] = None,
    ):
        self.supabase = supabase_client

        self.config = config or EngineConfig()

        self.action_executor = action_executor

        self._lock = threading.RLock()

        # --------------------------------------------------------------
        # Observation memory
        # --------------------------------------------------------------

        self._observations: Dict[
            str,
            Deque[Observation]
        ] = defaultdict(
            lambda: deque(maxlen=500)
        )

        # --------------------------------------------------------------
        # Baselines
        # --------------------------------------------------------------

        self._baselines: Dict[
            Tuple[str, str],
            Baseline
        ] = {}

        # --------------------------------------------------------------
        # Trends
        # --------------------------------------------------------------

        self._trends: Dict[
            Tuple[str, str],
            Trend
        ] = {}

        # --------------------------------------------------------------
        # Profiles
        # --------------------------------------------------------------

        self._brand_health: Dict[
            str,
            Dict[str, Any]
        ] = {}

        # --------------------------------------------------------------
        # Decisions
        # --------------------------------------------------------------

        self._decisions: Dict[
            str,
            Decision
        ] = {}

        self._plans: Dict[
            str,
            ActionPlan
        ] = {}

        # --------------------------------------------------------------
        # Decision memory
        # --------------------------------------------------------------

        self._decision_memory: List[
            DecisionMemory
        ] = []

        # --------------------------------------------------------------
        # Goals
        # --------------------------------------------------------------

        self._goals: Dict[
            str,
            Goal
        ] = {}

        # --------------------------------------------------------------
        # Strategies
        # --------------------------------------------------------------

        self._strategies: Dict[
            str,
            Strategy
        ] = {}

        # --------------------------------------------------------------
        # Experiments
        # --------------------------------------------------------------

        self._experiments: Dict[
            str,
            Experiment
        ] = {}

        # --------------------------------------------------------------
        # Action history
        # --------------------------------------------------------------

        self._last_action_at: Dict[
            Tuple[str, str],
            datetime
        ] = {}

        # --------------------------------------------------------------
        # State / hysteresis
        # --------------------------------------------------------------

        self._states: Dict[
            str,
            DecisionState
        ] = {}

        # --------------------------------------------------------------
        # Initialize default strategies
        # --------------------------------------------------------------

        self._register_default_strategies()

        logger.info(
            "Autonomous Condition Engine initialized"
        )

    # ==================================================================
    # STRATEGIES
    # ==================================================================

    def _register_default_strategies(self):
        """Register initial strategy candidates."""

        defaults = [
            Strategy(
                id="technical_tutorial",
                name="Technical Tutorials",
                description="Practical technical how-to content.",
            ),
            Strategy(
                id="technical_story",
                name="Technical Stories",
                description="Personal engineering experiences.",
            ),
            Strategy(
                id="technical_opinion",
                name="Technical Opinions",
                description="Strong engineering opinions.",
            ),
            Strategy(
                id="industry_news",
                name="Industry News",
                description="Relevant technology news.",
            ),
            Strategy(
                id="educational_tip",
                name="Educational Tips",
                description="Short practical technical lessons.",
            ),
        ]

        for strategy in defaults:
            self._strategies[strategy.id] = strategy

    # ==================================================================
    # OBSERVATION
    # ==================================================================

    def observe(
        self,
        observation: Observation,
    ) -> Dict[str, Any]:
        """
        Main observation entry point.

        This does NOT immediately execute actions.

        It analyzes the new evidence first.
        """

        observation.normalize()

        with self._lock:

            history = self._observations[
                observation.brand_id
            ]

            history.append(observation)

            baseline = self._calculate_baseline(
                observation.brand_id,
                "engagement",
            )

            trend = self._calculate_trend(
                observation.brand_id,
                "engagement",
            )

            anomaly = self._detect_anomaly(
                observation,
                baseline,
            )

            ratio = baseline.ratio(
                observation.engagement
            )

            state = self._determine_state(
                observation.brand_id,
                ratio,
                trend,
                anomaly,
            )

            confidence = self._calculate_confidence(
                observation=observation,
                baseline=baseline,
                trend=trend,
                anomaly=anomaly,
            )

            decision = self._make_decision(
                observation=observation,
                baseline=baseline,
                trend=trend,
                anomaly=anomaly,
                state=state,
                confidence=confidence,
            )

            self._decisions[
                decision.id
            ] = decision

            self._persist_decision(decision)

            return {
                "observation": self._serialize(observation),
                "baseline": self._serialize(baseline),
                "trend": self._serialize(trend),
                "anomaly": self._serialize(anomaly),
                "state": state.value,
                "confidence": confidence,
                "decision": self._serialize(decision),
            }

    # ==================================================================
    # BASELINE
    # ==================================================================

    def _calculate_baseline(
        self,
        brand_id: str,
        metric: str,
    ) -> Baseline:

        key = (brand_id, metric)

        history = list(
            self._observations.get(
                brand_id,
                []
            )
        )

        values = [
            self._metric_value(item, metric)
            for item in history
        ]

        values = [
            float(v)
            for v in values
            if v is not None
        ]

        values = values[
            -self.config.baseline_window:
        ]

        if not values:
            baseline = Baseline(
                metric=metric
            )

        else:
            baseline = Baseline(
                metric=metric,
                mean=round(
                    safe_mean(values),
                    4,
                ),
                median=round(
                    safe_median(values),
                    4,
                ),
                stddev=round(
                    safe_stddev(values),
                    4,
                ),
                minimum=min(values),
                maximum=max(values),
                sample_count=len(values),
            )

        self._baselines[key] = baseline

        return baseline

    # ==================================================================
    # TREND ANALYSIS
    # ==================================================================

    def _calculate_trend(
        self,
        brand_id: str,
        metric: str,
    ) -> Trend:

        history = list(
            self._observations.get(
                brand_id,
                []
            )
        )

        values = [
            self._metric_value(item, metric)
            for item in history
        ]

        values = [
            float(v)
            for v in values
            if v is not None
        ]

        if len(values) < 3:
            trend = Trend(
                metric=metric,
                confidence=0.1,
                sample_count=len(values),
            )

            self._trends[
                (brand_id, metric)
            ] = trend

            return trend

        recent = values[-5:]

        if len(recent) < 2:
            velocity = 0.0
        else:
            first = recent[0]
            last = recent[-1]

            if first == 0:
                velocity = 0.0
            else:
                velocity = (
                    last - first
                ) / abs(first)

        midpoint = len(recent) // 2

        first_half = recent[:midpoint]
        second_half = recent[midpoint:]

        first_mean = safe_mean(first_half)
        second_mean = safe_mean(second_half)

        if first_mean == 0:
            acceleration = 0.0
        else:
            acceleration = (
                second_mean - first_mean
            ) / abs(first_mean)

        if velocity >= self.config.strong_positive_velocity:
            direction = "rising"

        elif velocity <= self.config.strong_negative_velocity:
            direction = "falling"

        else:
            direction = "stable"

        confidence = clamp(
            len(values) / self.config.baseline_window
        )

        trend = Trend(
            metric=metric,
            velocity=round(velocity, 4),
            acceleration=round(
                acceleration,
                4,
            ),
            direction=direction,
            confidence=round(
                confidence,
                4,
            ),
            sample_count=len(values),
        )

        self._trends[
            (brand_id, metric)
        ] = trend

        return trend

    # ==================================================================
    # ANOMALY DETECTION
    # ==================================================================

    def _detect_anomaly(
        self,
        observation: Observation,
        baseline: Baseline,
    ) -> Optional[Anomaly]:

        if (
            baseline.sample_count
            < self.config.minimum_anomaly_samples
        ):
            return None

        value = observation.engagement

        z = baseline.z_score(value)

        if abs(z) < self.config.anomaly_z_score:
            return None

        if z >= 0:
            severity = "positive"

        else:
            severity = "negative"

        confidence = clamp(
            abs(z) / 6.0
        )

        return Anomaly(
            metric="engagement",
            value=value,
            expected=baseline.mean,
            z_score=round(z, 4),
            severity=severity,
            confidence=round(
                confidence,
                4,
            ),
        )

    # ==================================================================
    # MULTI-SIGNAL CONFIDENCE
    # ==================================================================

    def _calculate_confidence(
        self,
        observation: Observation,
        baseline: Baseline,
        trend: Trend,
        anomaly: Optional[Anomaly],
    ) -> float:

        confidence_components = []

        # --------------------------------------------------------------
        # Sample confidence
        # --------------------------------------------------------------

        sample_confidence = clamp(
            baseline.sample_count / 30
        )

        confidence_components.append(
            sample_confidence
        )

        # --------------------------------------------------------------
        # Baseline stability
        # --------------------------------------------------------------

        if baseline.mean > 0:
            coefficient_variation = (
                baseline.stddev
                / baseline.mean
            )

            stability = clamp(
                1.0 - coefficient_variation
            )

        else:
            stability = 0.2

        confidence_components.append(
            stability
        )

        # --------------------------------------------------------------
        # Trend confidence
        # --------------------------------------------------------------

        confidence_components.append(
            trend.confidence
        )

        # --------------------------------------------------------------
        # Anomaly evidence
        # --------------------------------------------------------------

        if anomaly:
            confidence_components.append(
                anomaly.confidence
            )

        else:
            confidence_components.append(
                0.5
            )

        # --------------------------------------------------------------
        # Metadata completeness
        # --------------------------------------------------------------

        metadata_score = 0.0

        checks = [
            bool(observation.topic),
            bool(observation.content_type),
            bool(observation.style),
            observation.posting_hour is not None,
        ]

        if checks:
            metadata_score = sum(checks) / len(checks)

        confidence_components.append(
            metadata_score
        )

        return round(
            safe_mean(
                confidence_components
            ),
            4,
        )

    # ==================================================================
    # STATE MACHINE
    # ==================================================================

    def _determine_state(
        self,
        brand_id: str,
        ratio: float,
        trend: Trend,
        anomaly: Optional[Anomaly],
    ) -> DecisionState:

        current = self._states.get(
            brand_id,
            DecisionState.OBSERVE,
        )

        # --------------------------------------------------------------
        # Critical condition
        # --------------------------------------------------------------

        if (
            ratio
            <= self.config.critical_performance_ratio
        ):
            new_state = DecisionState.CRITICAL

        # --------------------------------------------------------------
        # Strong positive performance
        # --------------------------------------------------------------

        elif (
            ratio
            >= self.config.boost_enter_threshold
        ):
            new_state = DecisionState.BOOST

        # --------------------------------------------------------------
        # Hysteresis: remain BOOST
        # --------------------------------------------------------------

        elif (
            current == DecisionState.BOOST
            and ratio
            >= self.config.boost_exit_threshold
        ):
            new_state = DecisionState.BOOST

        # --------------------------------------------------------------
        # Weak performance
        # --------------------------------------------------------------

        elif (
            ratio
            <= self.config.reduce_enter_threshold
        ):
            new_state = DecisionState.REDUCE

        # --------------------------------------------------------------
        # Hysteresis: remain REDUCE
        # --------------------------------------------------------------

        elif (
            current == DecisionState.REDUCE
            and ratio
            <= self.config.reduce_exit_threshold
        ):
            new_state = DecisionState.REDUCE

        # --------------------------------------------------------------
        # Rising trend
        # --------------------------------------------------------------

        elif trend.direction == "rising":
            new_state = DecisionState.MONITOR

        # --------------------------------------------------------------
        # Falling trend
        # --------------------------------------------------------------

        elif trend.direction == "falling":
            new_state = DecisionState.MONITOR

        else:
            new_state = DecisionState.OBSERVE

        self._states[
            brand_id
        ] = new_state

        return new_state

    # ==================================================================
    # DECISION ENGINE
    # ==================================================================

    def _make_decision(
        self,
        observation: Observation,
        baseline: Baseline,
        trend: Trend,
        anomaly: Optional[Anomaly],
        state: DecisionState,
        confidence: float,
    ) -> Decision:

        ratio = baseline.ratio(
            observation.engagement
        )

        evidence = {
            "engagement": observation.engagement,
            "baseline": baseline.mean,
            "engagement_ratio": round(
                ratio,
                4,
            ),
            "trend": {
                "direction": trend.direction,
                "velocity": trend.velocity,
                "acceleration": trend.acceleration,
            },
            "sample_count": baseline.sample_count,
        }

        if anomaly:
            evidence["anomaly"] = {
                "z_score": anomaly.z_score,
                "severity": anomaly.severity,
                "confidence": anomaly.confidence,
            }

        # --------------------------------------------------------------
        # CRITICAL
        # --------------------------------------------------------------

        if state == DecisionState.CRITICAL:

            action = "reduce_content_risk"

            reason = (
                "Performance is critically below "
                "the historical baseline."
            )

            risk = ActionRisk.MEDIUM

        # --------------------------------------------------------------
        # BOOST
        # --------------------------------------------------------------

        elif state == DecisionState.BOOST:

            action = "increase_similar_content"

            reason = (
                "Current performance significantly "
                "exceeds the historical baseline."
            )

            risk = ActionRisk.LOW

        # --------------------------------------------------------------
        # REDUCE
        # --------------------------------------------------------------

        elif state == DecisionState.REDUCE:

            action = "reduce_underperforming_content"

            reason = (
                "Current performance is materially "
                "below the historical baseline."
            )

            risk = ActionRisk.MEDIUM

        # --------------------------------------------------------------
        # RISING
        # --------------------------------------------------------------

        elif trend.direction == "rising":

            action = "monitor_positive_trend"

            reason = (
                "Performance is showing positive "
                "momentum."
            )

            risk = ActionRisk.LOW

        # --------------------------------------------------------------
        # FALLING
        # --------------------------------------------------------------

        elif trend.direction == "falling":

            action = "investigate_decline"

            reason = (
                "Performance is showing negative "
                "momentum."
            )

            risk = ActionRisk.LOW

        # --------------------------------------------------------------
        # OBSERVE
        # --------------------------------------------------------------

        else:

            action = None

            reason = (
                "Evidence is insufficient for a "
                "meaningful autonomous intervention."
            )

            risk = ActionRisk.LOW

        requires_approval = (
            risk in {
                ActionRisk.HIGH,
                ActionRisk.CRITICAL,
            }
            or confidence
            < self.config.minimum_confidence_for_action
        )

        decision = Decision(
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

        return decision

    # ==================================================================
    # ACTION PLANNING
    # ==================================================================

    def create_action_plan(
        self,
        decision: Decision,
    ) -> Optional[ActionPlan]:

        if not decision.recommended_action:
            return None

        action = decision.recommended_action

        # --------------------------------------------------------------
        # Cooldown
        # --------------------------------------------------------------

        if self._is_on_cooldown(
            decision.brand_id,
            action,
        ):
            logger.info(
                "Action %s is on cooldown for brand %s",
                action,
                decision.brand_id,
            )

            return None

        # --------------------------------------------------------------
        # Safety
        # --------------------------------------------------------------

        if not self._is_action_allowed(
            decision.risk,
            decision.confidence,
        ):
            logger.warning(
                "Action blocked by safety policy: %s",
                action,
            )

            return None

        # --------------------------------------------------------------
        # Parameters
        # --------------------------------------------------------------

        parameters = self._build_action_parameters(
            decision
        )

        expected = self._estimate_expected_outcome(
            decision
        )

        plan = ActionPlan(
            id=generate_id("plan"),
            decision_id=decision.id,
            brand_id=decision.brand_id,
            action=action,
            risk=decision.risk,
            confidence=decision.confidence,
            parameters=parameters,
            expected_outcome=expected,
        )

        self._plans[
            plan.id
        ] = plan

        return plan

    # ==================================================================
    # ACTION PARAMETERS
    # ==================================================================

    def _build_action_parameters(
        self,
        decision: Decision,
    ) -> Dict[str, Any]:

        action = decision.recommended_action

        if action == "increase_similar_content":

            return {
                "content_mix_change": 0.20,
                "preserve_topic": True,
                "preserve_style": True,
                "duration_posts": 5,
            }

        if action == "reduce_underperforming_content":

            return {
                "content_mix_change": -0.20,
                "duration_posts": 5,
            }

        if action == "reduce_content_risk":

            return {
                "content_mix_change": -0.30,
                "experimental_content": False,
                "duration_posts": 5,
            }

        if action == "investigate_decline":

            return {
                "run_experiment": True,
                "collect_more_data": True,
            }

        if action == "monitor_positive_trend":

            return {
                "increase_observation_frequency": True,
            }

        return {}

    # ==================================================================
    # EXPECTED OUTCOME
    # ==================================================================

    def _estimate_expected_outcome(
        self,
        decision: Decision,
    ) -> Dict[str, Any]:

        baseline = decision.evidence.get(
            "baseline",
            0,
        )

        ratio = decision.evidence.get(
            "engagement_ratio",
            1.0,
        )

        action = decision.recommended_action

        if action == "increase_similar_content":

            target = baseline * 1.10

        elif action == "reduce_underperforming_content":

            target = baseline * 0.95

        elif action == "reduce_content_risk":

            target = baseline * 0.85

        else:

            target = baseline

        return {
            "metric": "engagement",
            "baseline": baseline,
            "current_ratio": ratio,
            "target": round(target, 2),
        }

    # ==================================================================
    # SAFETY
    # ==================================================================

    def _is_action_allowed(
        self,
        risk: ActionRisk,
        confidence: float,
    ) -> bool:

        if confidence < self.config.minimum_confidence_for_action:
            return False

        if risk == ActionRisk.LOW:
            return self.config.allow_low_risk_autonomy

        if risk == ActionRisk.MEDIUM:
            return self.config.allow_medium_risk_autonomy

        if risk == ActionRisk.HIGH:
            return self.config.allow_high_risk_autonomy

        if risk == ActionRisk.CRITICAL:
            return self.config.allow_critical_autonomy

        return False

    # ==================================================================
    # COOLDOWNS
    # ==================================================================

    def _is_on_cooldown(
        self,
        brand_id: str,
        action: str,
    ) -> bool:

        key = (
            brand_id,
            action,
        )

        last = self._last_action_at.get(
            key
        )

        if not last:
            return False

        if action.startswith("increase"):
            minutes = (
                self.config.boost_cooldown_minutes
            )

        elif action.startswith("reduce"):
            minutes = (
                self.config.reduce_cooldown_minutes
            )

        else:
            minutes = (
                self.config.default_action_cooldown_minutes
            )

        return (
            utc_now() - last
        ) < timedelta(
            minutes=minutes
        )

    # ==================================================================
    # EXECUTION
    # ==================================================================

    def execute_plan(
        self,
        plan_id: str,
        approved: bool = False,
    ) -> ActionPlan:

        plan = self._plans.get(
            plan_id
        )

        if not plan:
            raise ValueError(
                f"Unknown action plan: {plan_id}"
            )

        # --------------------------------------------------------------
        # Approval
        # --------------------------------------------------------------

        if plan.risk in {
            ActionRisk.HIGH,
            ActionRisk.CRITICAL,
        } and not approved:

            plan.status = ActionStatus.REJECTED

            logger.warning(
                "Plan %s requires approval",
                plan.id,
            )

            return plan

        # --------------------------------------------------------------
        # Dry run
        # --------------------------------------------------------------

        if self.config.dry_run:

            logger.info(
                "[DRY RUN] Would execute: %s",
                plan.action,
            )

            plan.status = ActionStatus.APPROVED

            return plan

        # --------------------------------------------------------------
        # Executor
        # --------------------------------------------------------------

        if not self.action_executor:

            logger.warning(
                "No action executor configured"
            )

            plan.status = ActionStatus.APPROVED

            return plan

        try:

            result = self.action_executor(
                plan
            )

            plan.outcome = result

            plan.status = ActionStatus.EXECUTED

            plan.executed_at = utc_now()

            self._last_action_at[
                (
                    plan.brand_id,
                    plan.action,
                )
            ] = utc_now()

            logger.info(
                "Executed autonomous action: %s",
                plan.action,
            )

            return plan

        except Exception as exc:

            plan.status = ActionStatus.FAILED

            plan.outcome = {
                "error": str(exc)
            }

            logger.exception(
                "Autonomous action failed"
            )

            return plan

    # ==================================================================
    # GOALS
    # ==================================================================

    def register_goal(
        self,
        goal: Goal,
    ) -> Goal:

        self._goals[
            goal.id
        ] = goal

        logger.info(
            "Registered goal %s for brand %s",
            goal.name,
            goal.brand_id,
        )

        return goal

    def evaluate_goals(
        self,
        brand_id: str,
        metrics: Dict[str, float],
    ) -> List[Dict[str, Any]]:

        results = []

        for goal in self._goals.values():

            if goal.brand_id != brand_id:
                continue

            if goal.status not in {
                GoalStatus.ACTIVE,
                GoalStatus.AT_RISK,
            }:
                continue

            current = metrics.get(
                goal.metric
            )

            if current is None:
                continue

            progress = goal.calculate_progress(
                current
            )

            if progress >= 1.0:
                goal.status = GoalStatus.ACHIEVED

            elif progress < 0.25:
                goal.status = GoalStatus.AT_RISK

            results.append({
                "goal_id": goal.id,
                "name": goal.name,
                "metric": goal.metric,
                "current": current,
                "target": goal.target_value,
                "progress": progress,
                "status": goal.status.value,
            })

        return results

    # ==================================================================
    # BRAND HEALTH
    # ==================================================================

    def calculate_brand_health(
        self,
        brand_id: str,
    ) -> Dict[str, Any]:

        observations = list(
            self._observations.get(
                brand_id,
                []
            )
        )

        if not observations:

            health = {
                "score": 50.0,
                "status": "unknown",
                "components": {},
            }

            self._brand_health[
                brand_id
            ] = health

            return health

        baseline = self._calculate_baseline(
            brand_id,
            "engagement",
        )

        trend = self._calculate_trend(
            brand_id,
            "engagement",
        )

        recent = observations[-10:]

        recent_average = safe_mean([
            item.engagement
            for item in recent
        ])

        if baseline.mean > 0:
            performance_score = clamp(
                recent_average
                / baseline.mean
                / 2
            ) * 100
        else:
            performance_score = 50

        trend_score = 50

        if trend.direction == "rising":
            trend_score = 85

        elif trend.direction == "falling":
            trend_score = 25

        consistency_values = [
            item.engagement
            for item in recent
        ]

        consistency = 50

        if consistency_values:

            mean = safe_mean(
                consistency_values
            )

            std = safe_stddev(
                consistency_values
            )

            if mean > 0:
                cv = std / mean

                consistency = (
                    clamp(
                        1 - cv
                    )
                    * 100
                )

        score = (
            performance_score * 0.50
            + trend_score * 0.30
            + consistency * 0.20
        )

        score = round(
            clamp(
                score,
                0,
                100,
            ),
            2,
        )

        if score >= 80:
            status = "healthy"

        elif score >= 60:
            status = "stable"

        elif score >= 40:
            status = "watch"

        elif score >= 20:
            status = "weak"

        else:
            status = "critical"

        health = {
            "score": score,
            "status": status,
            "components": {
                "performance": round(
                    performance_score,
                    2,
                ),
                "trend": round(
                    trend_score,
                    2,
                ),
                "consistency": round(
                    consistency,
                    2,
                ),
            },
            "updated_at": iso_now(),
        }

        self._brand_health[
            brand_id
        ] = health

        return health

    # ==================================================================
    # STRATEGY SELECTION
    # ==================================================================

    def select_strategy(
        self,
        brand_id: str,
    ) -> Optional[Strategy]:

        strategies = [
            strategy
            for strategy in self._strategies.values()
            if strategy.enabled
        ]

        if not strategies:
            return None

        # --------------------------------------------------------------
        # Exploration
        # --------------------------------------------------------------

        if random.random() < self.config.exploration_rate:

            return random.choice(
                strategies
            )

        # --------------------------------------------------------------
        # Exploitation
        # --------------------------------------------------------------

        return max(
            strategies,
            key=lambda strategy: (
                strategy.reward
                + strategy.success_rate
            ),
        )

    # ==================================================================
    # STRATEGY OUTCOME
    # ==================================================================

    def record_strategy_result(
        self,
        strategy_id: str,
        reward: float,
        success: bool,
    ) -> None:

        strategy = self._strategies.get(
            strategy_id
        )

        if not strategy:
            return

        strategy.trials += 1

        if success:
            strategy.successes += 1
        else:
            strategy.failures += 1

        # Exponential moving reward update

        strategy.reward = (
            (
                1
                - self.config.learning_rate
            )
            * strategy.reward
            + self.config.learning_rate
            * reward
        )

        logger.info(
            "Strategy %s updated: reward=%.3f",
            strategy_id,
            strategy.reward,
        )

    # ==================================================================
    # EXPERIMENTS
    # ==================================================================

    def create_experiment(
        self,
        brand_id: str,
        name: str,
        variable: str,
        control: str,
        variant: str,
    ) -> Experiment:

        experiment = Experiment(
            id=generate_id("experiment"),
            brand_id=brand_id,
            name=name,
            variable=variable,
            control=control,
            variant=variant,
        )

        self._experiments[
            experiment.id
        ] = experiment

        return experiment

    def record_experiment_result(
        self,
        experiment_id: str,
        variant: str,
        result: float,
    ) -> Optional[Dict[str, Any]]:

        experiment = self._experiments.get(
            experiment_id
        )

        if not experiment:
            return None

        if variant == "control":

            experiment.control_results.append(
                float(result)
            )

        elif variant == "variant":

            experiment.variant_results.append(
                float(result)
            )

        else:

            raise ValueError(
                "variant must be "
                "'control' or 'variant'"
            )

        return self.evaluate_experiment(
            experiment_id
        )

    def evaluate_experiment(
        self,
        experiment_id: str,
    ) -> Dict[str, Any]:

        experiment = self._experiments.get(
            experiment_id
        )

        if not experiment:
            raise ValueError(
                f"Unknown experiment: {experiment_id}"
            )

        control = experiment.control_results

        variant = experiment.variant_results

        if (
            len(control)
            < experiment.minimum_samples
            or len(variant)
            < experiment.minimum_samples
        ):

            return {
                "status": "insufficient_data",
                "control_samples": len(control),
                "variant_samples": len(variant),
            }

        control_mean = safe_mean(
            control
        )

        variant_mean = safe_mean(
            variant
        )

        if control_mean == 0:

            improvement = 0.0

        else:

            improvement = (
                (
                    variant_mean
                    - control_mean
                )
                / abs(control_mean)
            )

        if improvement > 0.05:
            winner = "variant"

        elif improvement < -0.05:
            winner = "control"

        else:
            winner = "inconclusive"

        experiment.winner = winner

        if winner != "inconclusive":

            experiment.status = "completed"

        return {
            "status": experiment.status,
            "winner": winner,
            "control_mean": control_mean,
            "variant_mean": variant_mean,
            "improvement": round(
                improvement,
                4,
            ),
            "control_samples": len(control),
            "variant_samples": len(variant),
        }

    # ==================================================================
    # DECISION OUTCOME EVALUATION
    # ==================================================================

    def record_decision_outcome(
        self,
        decision_id: str,
        actual_value: float,
    ) -> Optional[DecisionMemory]:

        decision = self._decisions.get(
            decision_id
        )

        if not decision:
            return None

        expected = (
            decision.evidence
            .get(
                "baseline",
                0,
            )
        )

        # --------------------------------------------------------------
        # Determine whether outcome succeeded
        # --------------------------------------------------------------

        action = decision.recommended_action

        if action == "increase_similar_content":

            success = (
                actual_value
                >= expected * 1.10
            )

        elif action in {
            "reduce_underperforming_content",
            "reduce_content_risk",
        }:

            success = (
                actual_value
                >= expected * 0.95
            )

        else:

            success = (
                actual_value
                >= expected
            )

        memory = DecisionMemory(
            decision_id=decision_id,
            brand_id=decision.brand_id,
            action=action or "observe",
            reason=decision.reason,
            confidence=decision.confidence,
            expected_value=expected,
            actual_value=actual_value,
            success=success,
            evaluated_at=utc_now(),
        )

        self._decision_memory.append(
            memory
        )

        # --------------------------------------------------------------
        # Learn from outcome
        # --------------------------------------------------------------

        self._learn_from_decision(
            memory
        )

        self._persist_decision_memory(
            memory
        )

        return memory

    # ==================================================================
    # LEARNING
    # ==================================================================

    def _learn_from_decision(
        self,
        memory: DecisionMemory,
    ) -> None:

        if memory.actual_value is None:
            return

        if memory.expected_value <= 0:
            return

        reward = (
            memory.actual_value
            / memory.expected_value
        )

        reward = clamp(
            reward,
            0,
            2,
        )

        # Convert action to strategy where possible

        strategy_map = {
            "increase_similar_content":
                "technical_tutorial",
            "reduce_underperforming_content":
                "technical_tutorial",
        }

        strategy_id = strategy_map.get(
            memory.action
        )

        if strategy_id:

            self.record_strategy_result(
                strategy_id=strategy_id,
                reward=reward,
                success=bool(
                    memory.success
                ),
            )

    # ==================================================================
    # AUTONOMOUS CYCLE
    # ==================================================================

    def run_cycle(
        self,
        observations: Iterable[Observation],
    ) -> Dict[str, Any]:
        """
        Process multiple observations in one autonomous cycle.
        """

        results = []

        grouped: Dict[
            str,
            List[Observation]
        ] = defaultdict(list)

        for observation in observations:

            grouped[
                observation.brand_id
            ].append(observation)

            results.append(
                self.observe(
                    observation
                )
            )

        brand_reports = {}

        for brand_id in grouped:

            health = (
                self.calculate_brand_health(
                    brand_id
                )
            )

            goals = (
                self.evaluate_goals(
                    brand_id,
                    {
                        "engagement":
                            safe_mean([
                                item.engagement
                                for item
                                in grouped[brand_id]
                            ])
                    },
                )
            )

            strategy = (
                self.select_strategy(
                    brand_id
                )
            )

            brand_reports[
                brand_id
            ] = {
                "health": health,
                "goals": goals,
                "recommended_strategy": (
                    self._serialize(
                        strategy
                    )
                    if strategy
                    else None
                ),
            }

        return {
            "timestamp": iso_now(),
            "observations_processed": len(
                results
            ),
            "results": results,
            "brands": brand_reports,
        }

    # ==================================================================
    # DECISION EXPLANATION
    # ==================================================================

    def explain_decision(
        self,
        decision_id: str,
    ) -> Dict[str, Any]:

        decision = self._decisions.get(
            decision_id
        )

        if not decision:
            return {
                "error": "Decision not found"
            }

        return {
            "decision_id": decision.id,
            "brand_id": decision.brand_id,
            "state": decision.state.value,
            "confidence": decision.confidence,
            "reason": decision.reason,
            "recommended_action":
                decision.recommended_action,
            "risk": decision.risk.value,
            "requires_approval":
                decision.requires_approval,
            "evidence": decision.evidence,
            "created_at":
                decision.created_at.isoformat(),
        }

    # ==================================================================
    # DASHBOARD / STATUS
    # ==================================================================

    def get_status(
        self,
        brand_id: Optional[str] = None,
    ) -> Dict[str, Any]:

        if brand_id:

            observations = list(
                self._observations.get(
                    brand_id,
                    []
                )
            )

            state = self._states.get(
                brand_id,
                DecisionState.OBSERVE,
            )

            health = (
                self._brand_health.get(
                    brand_id
                )
            )

            return {
                "brand_id": brand_id,
                "state": state.value,
                "health": health,
                "observation_count":
                    len(observations),
                "active_goals": len([
                    goal
                    for goal in self._goals.values()
                    if (
                        goal.brand_id == brand_id
                        and goal.status
                        == GoalStatus.ACTIVE
                    )
                ]),
                "active_experiments": len([
                    experiment
                    for experiment
                    in self._experiments.values()
                    if (
                        experiment.brand_id
                        == brand_id
                        and experiment.status
                        == "running"
                    )
                ]),
            }

        return {
            "brands": len(
                self._observations
            ),
            "observations": sum(
                len(items)
                for items
                in self._observations.values()
            ),
            "decisions": len(
                self._decisions
            ),
            "plans": len(
                self._plans
            ),
            "goals": len(
                self._goals
            ),
            "experiments": len(
                self._experiments
            ),
            "strategies": len(
                self._strategies
            ),
            "decision_memory":
                len(
                    self._decision_memory
                ),
            "dry_run":
                self.config.dry_run,
            "timestamp":
                iso_now(),
        }

    # ==================================================================
    # METRIC EXTRACTION
    # ==================================================================

    @staticmethod
    def _metric_value(
        observation: Observation,
        metric: str,
    ) -> float:

        value = getattr(
            observation,
            metric,
            0.0,
        )

        try:
            return float(value)
        except Exception:
            return 0.0

    # ==================================================================
    # SERIALIZATION
    # ==================================================================

    @staticmethod
    def _serialize(
        value: Any,
    ) -> Any:

        if value is None:
            return None

        if isinstance(
            value,
            Enum,
        ):
            return value.value

        if isinstance(
            value,
            datetime,
        ):
            return value.isoformat()

        if hasattr(
            value,
            "__dataclass_fields__",
        ):
            result = {}

            for key, item in asdict(
                value
            ).items():

                result[key] = (
                    AutonomousConditionEngine
                    ._serialize(item)
                )

            return result

        if isinstance(
            value,
            dict,
        ):

            return {
                str(key):
                    AutonomousConditionEngine
                    ._serialize(item)

                for key, item
                in value.items()
            }

        if isinstance(
            value,
            (list, tuple),
        ):

            return [
                AutonomousConditionEngine
                ._serialize(item)

                for item
                in value
            ]

        return value

    # ==================================================================
    # SUPABASE PERSISTENCE
    # ==================================================================

    def _persist_decision(
        self,
        decision: Decision,
    ) -> None:

        if not self.supabase:
            return

        if not self.config.persist_decisions:
            return

        try:

            payload = {
                "id": decision.id,
                "brand_id":
                    decision.brand_id,
                "state":
                    decision.state.value,
                "confidence":
                    decision.confidence,
                "reason":
                    decision.reason,
                "recommended_action":
                    decision.recommended_action,
                "risk":
                    decision.risk.value,
                "requires_approval":
                    decision.requires_approval,
                "evidence":
                    decision.evidence,
                "created_at":
                    decision.created_at.isoformat(),
            }

            self.supabase.table(
                "autonomous_decisions"
            ).upsert(
                payload
            ).execute()

        except Exception as exc:

            logger.warning(
                "Failed to persist decision: %s",
                exc,
            )

    def _persist_decision_memory(
        self,
        memory: DecisionMemory,
    ) -> None:

        if not self.supabase:
            return

        if not self.config.persist_decisions:
            return

        try:

            payload = {
                "decision_id":
                    memory.decision_id,
                "brand_id":
                    memory.brand_id,
                "action":
                    memory.action,
                "reason":
                    memory.reason,
                "confidence":
                    memory.confidence,
                "expected_value":
                    memory.expected_value,
                "actual_value":
                    memory.actual_value,
                "success":
                    memory.success,
                "created_at":
                    memory.created_at.isoformat(),
                "evaluated_at":
                    (
                        memory.evaluated_at.isoformat()
                        if memory.evaluated_at
                        else None
                    ),
            }

            self.supabase.table(
                "autonomous_decision_memory"
            ).upsert(
                payload
            ).execute()

        except Exception as exc:

            logger.warning(
                "Failed to persist decision memory: %s",
                exc,
            )

    # ==================================================================
    # ADMIN / DEBUG
    # ==================================================================

    def clear_brand_cache(
        self,
        brand_id: str,
    ) -> None:

        with self._lock:

            self._observations.pop(
                brand_id,
                None,
            )

            self._brand_health.pop(
                brand_id,
                None,
            )

            self._states.pop(
                brand_id,
                None,
            )

            keys = [
                key
                for key
                in self._baselines
                if key[0] == brand_id
            ]

            for key in keys:
                self._baselines.pop(
                    key,
                    None,
                )

            keys = [
                key
                for key
                in self._trends
                if key[0] == brand_id
            ]

            for key in keys:
                self._trends.pop(
                    key,
                    None,
                )

    def export_state(self) -> Dict[str, Any]:
        """Export complete in-memory engine state."""

        return {
            "timestamp": iso_now(),
            "status": self.get_status(),
            "states": {
                brand_id:
                    state.value

                for brand_id, state
                in self._states.items()
            },
            "health": self._serialize(
                self._brand_health
            ),
            "strategies": self._serialize(
                self._strategies
            ),
            "goals": self._serialize(
                self._goals
            ),
            "experiments": self._serialize(
                self._experiments
            ),
        }


# ============================================================================
# DEFAULT ACTION EXECUTOR
# ============================================================================

def default_action_executor(
    plan: ActionPlan,
) -> Dict[str, Any]:
    """
    Safe placeholder executor.

    Replace this with your actual scheduler/content-engine integration.

    Example actions:

        increase_similar_content
        reduce_underperforming_content
        reduce_content_risk
        investigate_decline
        monitor_positive_trend
    """

    logger.info(
        "Action executor received: %s",
        plan.action,
    )

    return {
        "success": True,
        "action": plan.action,
        "parameters": plan.parameters,
        "executed_at": iso_now(),
        "message": (
            "Action accepted by autonomous executor."
        ),
    }


# ============================================================================
# SINGLETON
# ============================================================================

autonomous_engine = AutonomousConditionEngine(
    config=EngineConfig(
        dry_run=False,
        allow_low_risk_autonomy=True,
        allow_medium_risk_autonomy=True,
        allow_high_risk_autonomy=False,
        allow_critical_autonomy=False,
    ),
    action_executor=default_action_executor,
)


# ============================================================================
# CONVENIENCE API
# ============================================================================

def evaluate_engagement(
    brand_id: str,
    post_id: str,
    likes: int = 0,
    comments: int = 0,
    shares: int = 0,
    clicks: int = 0,
    impressions: int = 0,
    reach: int = 0,
    topic: str = "",
    content_type: str = "",
    style: str = "",
    hook_type: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:

    observation = Observation(
        brand_id=brand_id,
        post_id=post_id,
        likes=likes,
        comments=comments,
        shares=shares,
        clicks=clicks,
        impressions=impressions,
        reach=reach,
        topic=topic,
        content_type=content_type,
        style=style,
        hook_type=hook_type,
        metadata=metadata or {},
    )

    return autonomous_engine.observe(
        observation
    )


def get_engine_status(
    brand_id: Optional[str] = None,
) -> Dict[str, Any]:

    return autonomous_engine.get_status(
        brand_id
    )


# ============================================================================
# EXAMPLE GOAL
# ============================================================================

def register_default_goal(
    brand_id: str,
) -> Goal:

    goal = Goal(
        id=generate_id("goal"),
        brand_id=brand_id,
        name="Increase meaningful engagement",
        metric="engagement",
        target_value=120.0,
        baseline_value=100.0,
        deadline=(
            utc_now()
            + timedelta(days=30)
        ),
    )

    return autonomous_engine.register_goal(
        goal
    )


# ============================================================================
# EXAMPLE EXPERIMENT
# ============================================================================

def create_hook_experiment(
    brand_id: str,
) -> Experiment:

    return autonomous_engine.create_experiment(
        brand_id=brand_id,
        name="Hook Performance Experiment",
        variable="hook_type",
        control="direct_technical_hook",
        variant="story_based_hook",
    )


# ============================================================================
# HEALTH CHECK
# ============================================================================

def health_check() -> Dict[str, Any]:

    return {
        "service": "autonomous_condition_engine",
        "status": "healthy",
        "version": "2.0",
        "timestamp": iso_now(),
        "engine": autonomous_engine.get_status(),
    }


# ============================================================================
# MODULE EXPORTS
# ============================================================================

__all__ = [
    "EngineConfig",
    "Observation",
    "Baseline",
    "Trend",
    "Anomaly",
    "Goal",
    "Strategy",
    "Decision",
    "ActionPlan",
    "Experiment",
    "DecisionMemory",
    "DecisionState",
    "ActionRisk",
    "ActionStatus",
    "GoalStatus",
    "AutonomousConditionEngine",
    "autonomous_engine",
    "evaluate_engagement",
    "get_engine_status",
    "register_default_goal",
    "create_hook_experiment",
    "health_check",
]
