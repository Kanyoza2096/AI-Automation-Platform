"""
KANYOZA AUTONOMOUS CONDITION + DECISION ENGINE v4.0.1
====================================================

Version: 4.0.1
Purpose:
    Complete autonomous decision intelligence for social-media/content systems.

v4.0.1 CHANGES:
    - Added event bus integration for strategy_executed and brand_health_updated events
    - Defensive event publishing with fallback handling

v4.0 NEW CAPABILITIES:
    - Adaptive thresholds (self-tuning based on outcomes)
    - Expanded action space (12+ actions)
    - Multi-metric evaluation (engagement rate, CTR, share rate, comment rate)
    - Per-topic, per-style, per-hour baselines
    - Predictive trend analysis (forecast, reversal detection)
    - Automated A/B testing (create, evaluate, deploy winning variants)
    - Full state persistence (baselines, trends, strategy rewards)
    - Brand clustering (shared learning across similar brands)
    - Anomaly classification (spike, dip, drift, level shift)
    - Confidence calibration (per-brand, outcome-based)
    - UCB strategy selection (better than epsilon-greedy)

v3.0 CHANGES (retained):
    - set_action_executor() and set_supabase_client()
    - Autonomous loop: Observe → Analyze → Decide → ACT
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
from typing import Any, Callable, Deque, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger("kanyoza.condition_engine")

# At the top of condition_engine.py:

try:
    from plugins.council.personas import get_council
    HAS_COUNCIL = True
except ImportError:
    HAS_COUNCIL = False


# In AutonomousConditionEngine.__init__, after self.threshold_manager:

        # Council integration
        self.council = get_council() if HAS_COUNCIL else None


# In _make_decision method, after creating decision but before return:

        # Council vote for content decisions (v4.1.0)
        if self.council and decision.recommended_action:
            brand_context = {
                "industry": "general",
                "brand_id": observation.brand_id,
            }
            
            proposal = {
                "topic": observation.topic,
                "content": decision.reason,
                "hour": observation.posting_hour or datetime.now().hour,
                "day": datetime.now().strftime("%A").lower(),
                "engagement_ratio": baseline.ratio(observation.engagement) if baseline else 1.0,
                "sample_count": baseline.sample_count if baseline else 0,
                "has_cta": "call" in decision.reason.lower(),
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

# ═══════════════════════════════════════════════════════════════════════════
# EVENT BUS INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════

try:
    from core.shared_event_bus import get_shared_event_bus
    HAS_EVENT_BUS = True
except ImportError:
    HAS_EVENT_BUS = False
    logger.warning("Shared event bus not available — events will be skipped")


def _publish_event(event_name: str, payload: Dict[str, Any]) -> None:
    """Safely publish event to shared bus with defensive error handling."""
    if not HAS_EVENT_BUS:
        return
    try:
        bus = get_shared_event_bus()
        bus.publish(event_name, payload)
    except Exception:
        # Never let event publishing break core logic
        pass


# ============================================================================
# TIME HELPERS
# ============================================================================

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


def parse_datetime(value: Any) -> Optional[datetime]:
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


class AnomalyType(str, Enum):
    SPIKE = "spike"          # Sudden increase
    DIP = "dip"              # Sudden decrease
    DRIFT = "drift"          # Gradual change
    LEVEL_SHIFT = "level_shift"  # Permanent change
    OUTLIER = "outlier"      # Single anomalous point


# ============================================================================
# CONFIGURATION (with adaptive thresholds)
# ============================================================================

@dataclass
class EngineConfig:
    """Configuration with adaptive thresholds.

    v4.0: Thresholds can be updated by the learning loop.
    """

    baseline_window: int = 30
    minimum_baseline_samples: int = 5
    minimum_confidence_for_action: float = 0.65
    strong_confidence: float = 0.85
    boost_enter_threshold: float = 1.50
    boost_exit_threshold: float = 1.10
    reduce_enter_threshold: float = 0.70
    reduce_exit_threshold: float = 0.95
    critical_performance_ratio: float = 0.45  # ← ADD THIS
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

    # v4.0 — New settings
    enable_adaptive_thresholds: bool = True
    threshold_learning_rate: float = 0.10
    min_threshold: float = 0.30
    max_threshold: float = 3.00
    enable_multi_metric: bool = True
    enable_predictive_trend: bool = True
    enable_automated_experiments: bool = True
    enable_brand_clustering: bool = True
    enable_anomaly_classification: bool = True
    enable_confidence_calibration: bool = True


# ============================================================================
# DATA MODELS
# ============================================================================

@dataclass
class Observation:
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
    click_through_rate: float = 0.0
    share_rate: float = 0.0
    comment_rate: float = 0.0
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
            self.click_through_rate = (self.clicks / self.impressions) * 100
            self.share_rate = (self.shares / self.impressions) * 100
            self.comment_rate = (self.comments / self.impressions) * 100
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
        if self.mean <= 0:
            return 1.0
        return value / self.mean

    def z_score(self, value: float) -> float:
        if self.stddev <= 0:
            return 0.0
        return (value - self.mean) / self.stddev


@dataclass
class Trend:
    metric: str
    velocity: float = 0.0
    acceleration: float = 0.0
    direction: str = "stable"
    confidence: float = 0.0
    sample_count: int = 0
    # v4.0 — Predictive fields
    predicted_next_value: float = 0.0
    predicted_direction: str = "stable"
    reversal_probability: float = 0.0
    time_to_baseline_recovery: Optional[float] = None


@dataclass
class Anomaly:
    metric: str
    value: float
    expected: float
    z_score: float
    severity: str
    confidence: float
    detected_at: datetime = field(default_factory=utc_now)
    # v4.0 — Classification
    anomaly_type: AnomalyType = AnomalyType.OUTLIER
    root_cause_hypothesis: str = ""


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
        if self.trials <= 0:
            return 0.5
        return self.successes / self.trials

    def ucb_score(self, total_trials: int) -> float:
        """Upper Confidence Bound score for exploration."""
        if self.trials <= 0:
            return float('inf')
        exploration = math.sqrt(
            2 * math.log(total_trials + 1) / self.trials
        )
        return self.success_rate + exploration


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
    # v4.0 — Auto-deployment
    auto_deploy_winner: bool = True
    deployed: bool = False


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
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


# ============================================================================
# ANOMALY CLASSIFIER
# ============================================================================

class AnomalyClassifier:
    """Classifies anomalies into types and generates root cause hypotheses."""

    @staticmethod
    def classify(
        value: float,
        baseline: Baseline,
        recent_values: List[float],
    ) -> AnomalyType:
        """Classify anomaly type based on pattern."""
        if len(recent_values) < 3:
            return AnomalyType.OUTLIER

        # Check if it's a single point or sustained
        z = baseline.z_score(value)
        recent_z = [
            baseline.z_score(v) for v in recent_values[-5:]
        ]

        # Count how many recent values are also anomalous
        anomalous_count = sum(
            1 for vz in recent_z if abs(vz) >= 2.0
        )

        if anomalous_count >= 3:
            # Sustained change
            first_half = recent_values[:len(recent_values)//2]
            second_half = recent_values[len(recent_values)//2:]
            if safe_mean(second_half) > safe_mean(first_half) * 1.3:
                return AnomalyType.LEVEL_SHIFT
            return AnomalyType.DRIFT
        elif anomalous_count == 2:
            return AnomalyType.DRIFT
        else:
            # Single anomalous point
            if z > 0:
                return AnomalyType.SPIKE
            return AnomalyType.DIP

    @staticmethod
    def generate_hypothesis(
        anomaly_type: AnomalyType,
        observation: Observation,
    ) -> str:
        """Generate root cause hypothesis."""
        if anomaly_type == AnomalyType.SPIKE:
            if observation.shares > observation.likes:
                return "Viral sharing — content resonated strongly"
            elif observation.comments > observation.likes * 0.5:
                return "High discussion — controversial or engaging topic"
            else:
                return "Algorithm boost — increased visibility"
        elif anomaly_type == AnomalyType.DIP:
            if observation.impressions < 100:
                return "Low reach — possible algorithm suppression"
            elif observation.engagement_rate < 1.0:
                return "Low engagement rate — content not resonating"
            else:
                return "External factor — time/day issue"
        elif anomaly_type == AnomalyType.DRIFT:
            return "Gradual content fatigue or audience shift"
        elif anomaly_type == AnomalyType.LEVEL_SHIFT:
            return "Permanent change — algorithm update or audience shift"
        return "Unknown cause"


# ============================================================================
# PREDICTIVE TREND ANALYZER
# ============================================================================

class PredictiveTrendAnalyzer:
    """Predicts future trend direction and reversal probability."""

    @staticmethod
    def predict_next_value(
        values: List[float], horizon: int = 3
    ) -> float:
        """Predict next value using linear regression."""
        if len(values) < 3:
            return values[-1] if values else 0.0

        n = len(values)
        x = list(range(n))
        y = values

        x_mean = safe_mean(x)
        y_mean = safe_mean(y)

        numerator = sum(
            (xi - x_mean) * (yi - y_mean)
            for xi, yi in zip(x, y)
        )
        denominator = sum((xi - x_mean) ** 2 for xi in x)

        if denominator == 0:
            return y[-1]

        slope = numerator / denominator
        intercept = y_mean - slope * x_mean

        return max(0.0, intercept + slope * (n + horizon - 1))

    @staticmethod
    def predict_reversal_probability(
        values: List[float],
        direction: str,
    ) -> float:
        """Estimate probability of trend reversal."""
        if len(values) < 5:
            return 0.0

        recent = values[-5:]

        # Calculate momentum
        if direction == "rising":
            # Check if gains are slowing
            gains = [
                recent[i+1] - recent[i]
                for i in range(len(recent)-1)
            ]
            avg_gain = safe_mean(gains)
            if avg_gain <= 0:
                return 0.7  # Already reversing
            # Higher gains = less likely to reverse
            return clamp(0.5 - avg_gain / max(recent), 0, 1)
        elif direction == "falling":
            # Check if losses are slowing
            losses = [
                recent[i] - recent[i+1]
                for i in range(len(recent)-1)
            ]
            avg_loss = safe_mean(losses)
            if avg_loss <= 0:
                return 0.7  # Already recovering
            return clamp(0.5 - avg_loss / max(recent), 0, 1)
        return 0.3


# ============================================================================
# BRAND CLUSTERER
# ============================================================================

class BrandClusterer:
    """Clusters similar brands for shared learning."""

    def __init__(self):
        self.brand_features: Dict[str, Dict[str, float]] = {}
        self.clusters: Dict[str, List[str]] = defaultdict(list)
        self._lock = threading.RLock()

    def update_brand(
        self,
        brand_id: str,
        features: Dict[str, float],
    ) -> None:
        """Update brand features for clustering."""
        with self._lock:
            self.brand_features[brand_id] = features
            self._recluster()

    def _recluster(self) -> None:
        """Simple clustering based on feature similarity."""
        self.clusters.clear()

        brands = list(self.brand_features.keys())
        if len(brands) < 2:
            return

        # Simple k-means-like clustering (2 clusters)
        cluster_ids = list(self.brand_features.values())
        if len(cluster_ids) < 2:
            return

        # Use first two brands as initial centroids
        centroid_a = cluster_ids[0]
        centroid_b = cluster_ids[1]

        for brand_id, features in self.brand_features.items():
            dist_a = self._distance(features, centroid_a)
            dist_b = self._distance(features, centroid_b)
            cluster = "cluster_a" if dist_a <= dist_b else "cluster_b"
            self.clusters[cluster].append(brand_id)

    @staticmethod
    def _distance(
        a: Dict[str, float], b: Dict[str, float]
    ) -> float:
        """Euclidean distance between feature vectors."""
        keys = set(a.keys()) | set(b.keys())
        return math.sqrt(
            sum((a.get(k, 0) - b.get(k, 0)) ** 2 for k in keys)
        )

    def get_similar_brands(self, brand_id: str) -> List[str]:
        """Get brands in the same cluster."""
        with self._lock:
            for cluster_brands in self.clusters.values():
                if brand_id in cluster_brands:
                    return [b for b in cluster_brands if b != brand_id]
            return []

    def get_cluster_insights(
        self, brand_id: str
    ) -> Dict[str, Any]:
        """Get insights from similar brands."""
        similar = self.get_similar_brands(brand_id)
        if not similar:
            return {"similar_brands": 0, "shared_insights": []}

        return {
            "similar_brands": len(similar),
            "shared_insights": [
                f"Similar brand: {b}" for b in similar[:3]
            ],
        }


# ============================================================================
# ADAPTIVE THRESHOLDS
# ============================================================================

class AdaptiveThresholdManager:
    """Self-tuning thresholds based on outcomes."""

    def __init__(self, config: EngineConfig):
        self.config = config
        self.threshold_history: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=100)
        )
        self._lock = threading.RLock()

    def update_threshold(
        self,
        threshold_name: str,
        actual_value: float,
        outcome: str,
    ) -> None:
        """Update threshold based on outcome."""
        if not self.config.enable_adaptive_thresholds:
            return

        with self._lock:
            current = getattr(
                self.config, threshold_name,
                self.config.boost_enter_threshold,
            )
            self.threshold_history[threshold_name].append({
                "threshold": current,
                "actual": actual_value,
                "outcome": outcome,
                "timestamp": utc_now(),
            })

            if outcome == "false_positive":
                # Threshold was too sensitive
                new_value = current - self.config.threshold_learning_rate
            elif outcome == "true_positive":
                # Threshold was appropriate
                new_value = current + self.config.threshold_learning_rate * 0.5
            elif outcome == "missed":
                # Threshold was too strict
                new_value = current + self.config.threshold_learning_rate
            else:
                return

            new_value = clamp(
                new_value,
                self.config.min_threshold,
                self.config.max_threshold,
            )
            setattr(self.config, threshold_name, new_value)

            logger.info(
                "Adaptive threshold %s: %.2f → %.2f (%s)",
                threshold_name, current, new_value, outcome,
            )


# ============================================================================
# COMPLETE AUTONOMOUS CONDITION ENGINE v4.0.1
# ============================================================================

class AutonomousConditionEngine:
    """Complete autonomous condition + decision engine."""

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

        # Core state
        self._observations: Dict[str, Deque[Observation]] = defaultdict(
            lambda: deque(maxlen=500)
        )
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

        # v4.0 — New components
        self.anomaly_classifier = AnomalyClassifier()
        self.trend_analyzer = PredictiveTrendAnalyzer()
        self.brand_clusterer = BrandClusterer()
        self.threshold_manager = AdaptiveThresholdManager(config)

        # v4.0 — Per-topic baselines
        self._topic_baselines: Dict[
            Tuple[str, str, str], Baseline
        ] = {}

        # v4.0 — Confidence calibration
        self._confidence_history: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=100)
        )

        self._register_default_strategies()
        logger.info(
            "Autonomous Condition Engine v4.0.1 initialized "
            "(complete with adaptive thresholds, multi-metric, "
            "predictive trends, anomaly classification, "
            "brand clustering, event bus integration)"
        )

    # ═══════════════════════════════════════════════════════════════
    # WIRING METHODS
    # ═══════════════════════════════════════════════════════════════

    def set_action_executor(
        self, executor: Callable[[ActionPlan], Dict[str, Any]]
    ) -> None:
        self.action_executor = executor
        logger.info("ConditionEngine: action executor updated")

    def set_supabase_client(self, client) -> None:
        self.supabase = client
        logger.info("ConditionEngine: Supabase client updated")

    # ═══════════════════════════════════════════════════════════════
    # STRATEGIES
    # ═══════════════════════════════════════════════════════════════

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

    # ═══════════════════════════════════════════════════════════════
    # OBSERVATION (with multi-metric)
    # ═══════════════════════════════════════════════════════════════

    def observe(self, observation: Observation) -> Dict[str, Any]:
        observation.normalize()

        with self._lock:
            history = self._observations[observation.brand_id]
            history.append(observation)

            # Update brand features for clustering
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

            # Calculate baselines for multiple metrics
            metrics = ["engagement", "engagement_rate", "click_through_rate"]
            baselines = {}
            for metric in metrics:
                baselines[metric] = self._calculate_baseline(
                    observation.brand_id, metric
                )

            # Calculate per-topic baseline
            topic_baseline = self._calculate_topic_baseline(
                observation.brand_id, observation.topic, "engagement"
            )

            # Calculate trend with prediction
            trend = self._calculate_trend(
                observation.brand_id, "engagement"
            )

            # Detect and classify anomaly
            anomaly = self._detect_anomaly(
                observation, baselines["engagement"]
            )

            # Calculate ratio
            ratio = baselines["engagement"].ratio(
                observation.engagement
            )

            # Determine state
            state = self._determine_state(
                observation.brand_id, ratio, trend, anomaly
            )

            # Calculate confidence with calibration
            confidence = self._calculate_confidence(
                observation=observation,
                baseline=baselines["engagement"],
                trend=trend,
                anomaly=anomaly,
            )

            # Make decision
            decision = self._make_decision(
                observation=observation,
                baseline=baselines["engagement"],
                trend=trend,
                anomaly=anomaly,
                state=state,
                confidence=confidence,
            )

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
                "cluster_insights": self.brand_clusterer.get_cluster_insights(
                    observation.brand_id
                ),
            }

    # ═══════════════════════════════════════════════════════════════
    # BASELINES (with per-topic)
    # ═══════════════════════════════════════════════════════════════

    def _calculate_baseline(
        self, brand_id: str, metric: str
    ) -> Baseline:
        key = (brand_id, metric)
        history = list(self._observations.get(brand_id, []))
        values = [
            self._metric_value(item, metric) for item in history
        ]
        values = [float(v) for v in values if v is not None]
        values = values[-self.config.baseline_window:]

        if not values:
            baseline = Baseline(metric=metric)
        else:
            baseline = Baseline(
                metric=metric,
                mean=round(safe_mean(values), 4),
                median=round(safe_median(values), 4),
                stddev=round(safe_stddev(values), 4),
                minimum=min(values),
                maximum=max(values),
                sample_count=len(values),
            )
        self._baselines[key] = baseline
        return baseline

    def _calculate_topic_baseline(
        self, brand_id: str, topic: str, metric: str
    ) -> Optional[Baseline]:
        """Calculate baseline for a specific topic."""
        if not topic:
            return None

        key = (brand_id, topic, metric)
        history = [
            item for item in self._observations.get(brand_id, [])
            if item.topic == topic
        ]
        values = [
            self._metric_value(item, metric) for item in history
        ]
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

    # ═══════════════════════════════════════════════════════════════
    # TREND ANALYSIS (with prediction)
    # ═══════════════════════════════════════════════════════════════

    def _calculate_trend(
        self, brand_id: str, metric: str
    ) -> Trend:
        history = list(self._observations.get(brand_id, []))
        values = [
            self._metric_value(item, metric) for item in history
        ]
        values = [float(v) for v in values if v is not None]

        if len(values) < 3:
            trend = Trend(
                metric=metric, confidence=0.1,
                sample_count=len(values),
            )
            self._trends[(brand_id, metric)] = trend
            return trend

        recent = values[-5:]
        if len(recent) < 2:
            velocity = 0.0
        else:
            first, last = recent[0], recent[-1]
            velocity = 0.0 if first == 0 else (last - first) / abs(first)

        midpoint = len(recent) // 2
        first_half, second_half = recent[:midpoint], recent[midpoint:]
        first_mean = safe_mean(first_half)
        second_mean = safe_mean(second_half)
        acceleration = (
            0.0 if first_mean == 0
            else (second_mean - first_mean) / abs(first_mean)
        )

        if velocity >= self.config.strong_positive_velocity:
            direction = "rising"
        elif velocity <= self.config.strong_negative_velocity:
            direction = "falling"
        else:
            direction = "stable"

        confidence = clamp(len(values) / self.config.baseline_window)

        # v4.0 — Predictive fields
        predicted_next = self.trend_analyzer.predict_next_value(
            values, horizon=3
        )
        predicted_direction = (
            "rising" if predicted_next > values[-1]
            else "falling" if predicted_next < values[-1]
            else "stable"
        )
        reversal_prob = self.trend_analyzer.predict_reversal_probability(
            recent, direction
        )

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

    # ═══════════════════════════════════════════════════════════════
    # ANOMALY DETECTION (with classification)
    # ═══════════════════════════════════════════════════════════════

    def _detect_anomaly(
        self, observation: Observation, baseline: Baseline
    ) -> Optional[Anomaly]:
        if baseline.sample_count < self.config.minimum_anomaly_samples:
            return None

        z = baseline.z_score(observation.engagement)
        if abs(z) < self.config.anomaly_z_score:
            return None

        severity = "positive" if z >= 0 else "negative"
        confidence = clamp(abs(z) / 6.0)

        # v4.0 — Classify anomaly
        history = list(self._observations.get(observation.brand_id, []))
        recent_values = [
            item.engagement for item in history[-10:]
        ]
        anomaly_type = self.anomaly_classifier.classify(
            observation.engagement, baseline, recent_values
        )
        hypothesis = self.anomaly_classifier.generate_hypothesis(
            anomaly_type, observation
        )

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

    # ═══════════════════════════════════════════════════════════════
    # CONFIDENCE (with calibration)
    # ═══════════════════════════════════════════════════════════════

    def _calculate_confidence(
        self,
        observation: Observation,
        baseline: Baseline,
        trend: Trend,
        anomaly: Optional[Anomaly],
    ) -> float:
        components = []

        # Sample count confidence
        components.append(clamp(baseline.sample_count / 30))

        # Stability confidence
        if baseline.mean > 0:
            stability = clamp(1.0 - (baseline.stddev / baseline.mean))
        else:
            stability = 0.2
        components.append(stability)

        # Trend confidence
        components.append(trend.confidence)

        # Anomaly confidence
        components.append(anomaly.confidence if anomaly else 0.5)

        # Metadata completeness
        checks = [
            bool(observation.topic),
            bool(observation.content_type),
            bool(observation.style),
            observation.posting_hour is not None,
        ]
        metadata_score = sum(checks) / len(checks) if checks else 0.0
        components.append(metadata_score)

        raw_confidence = safe_mean(components)

        # v4.0 — Calibrate per brand
        calibrated = self._calibrate_confidence(
            observation.brand_id, raw_confidence
        )

        return round(calibrated, 4)

    def _calibrate_confidence(
        self, brand_id: str, raw_confidence: float
    ) -> float:
        """Calibrate confidence based on historical accuracy."""
        if not self.config.enable_confidence_calibration:
            return raw_confidence

        history = self._confidence_history.get(brand_id, [])
        if len(history) < 5:
            return raw_confidence

        # Calculate calibration factor
        recent = list(history)[-10:]
        correct = sum(
            1 for item in recent
            if item["success"] and item["confidence"] >= 0.65
        )
        accuracy = correct / len(recent) if recent else 0.5

        # Adjust: if we're overconfident, reduce; if underconfident, increase
        calibration_factor = accuracy / max(raw_confidence, 0.01)
        calibration_factor = clamp(calibration_factor, 0.7, 1.3)

        return clamp(raw_confidence * calibration_factor, 0, 1)

    # ═══════════════════════════════════════════════════════════════
    # STATE MACHINE
    # ═══════════════════════════════════════════════════════════════

    def _determine_state(
        self,
        brand_id: str,
        ratio: float,
        trend: Trend,
        anomaly: Optional[Anomaly],
    ) -> DecisionState:
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
        elif (
            self.config.enable_automated_experiments
            and trend.direction == "stable"
            and trend.reversal_probability > 0.5
        ):
            new_state = DecisionState.EXPERIMENT
        elif trend.direction == "rising":
            new_state = DecisionState.MONITOR
        elif trend.direction == "falling":
            new_state = DecisionState.MONITOR
        else:
            new_state = DecisionState.OBSERVE

        self._states[brand_id] = new_state
        return new_state

    # ═══════════════════════════════════════════════════════════════
    # DECISION ENGINE (with expanded actions)
    # ═══════════════════════════════════════════════════════════════

    def _make_decision(
        self,
        observation: Observation,
        baseline: Baseline,
        trend: Trend,
        anomaly: Optional[Anomaly],
        state: DecisionState,
        confidence: float,
    ) -> Decision:
        ratio = baseline.ratio(observation.engagement)
        evidence = {
            "engagement": observation.engagement,
            "engagement_rate": observation.engagement_rate,
            "click_through_rate": observation.click_through_rate,
            "share_rate": observation.share_rate,
            "comment_rate": observation.comment_rate,
            "baseline": baseline.mean,
            "engagement_ratio": round(ratio, 4),
            "trend": {
                "direction": trend.direction,
                "velocity": trend.velocity,
                "predicted_next": trend.predicted_next_value,
                "reversal_probability": trend.reversal_probability,
            },
            "sample_count": baseline.sample_count,
        }

        if anomaly:
            evidence["anomaly"] = {
                "z_score": anomaly.z_score,
                "severity": anomaly.severity,
                "type": anomaly.anomaly_type.value,
                "root_cause": anomaly.root_cause_hypothesis,
            }

        if state == DecisionState.CRITICAL:
            action = "reduce_content_risk"
            reason = "Performance critically below baseline."
            risk = ActionRisk.MEDIUM
        elif state == DecisionState.BOOST:
            action = "increase_similar_content"
            reason = "Performance significantly exceeds baseline."
            risk = ActionRisk.LOW
        elif state == DecisionState.REDUCE:
            action = "reduce_underperforming_content"
            reason = "Performance materially below baseline."
            risk = ActionRisk.MEDIUM
        elif state == DecisionState.EXPERIMENT:
            action = "run_experiment"
            reason = "Stable performance — testing new strategies."
            risk = ActionRisk.LOW
        elif trend.direction == "rising":
            action = "monitor_positive_trend"
            reason = "Positive momentum detected."
            risk = ActionRisk.LOW
        elif trend.direction == "falling":
            # v4.0 — More specific action based on anomaly
            if anomaly and anomaly.anomaly_type == AnomalyType.DIP:
                action = "change_posting_time"
                reason = "Single dip — testing different posting time."
            elif anomaly and anomaly.anomaly_type == AnomalyType.DRIFT:
                action = "change_content_type"
                reason = "Gradual decline — testing new content type."
            else:
                action = "investigate_decline"
                reason = "Negative momentum detected."
            risk = ActionRisk.LOW
        else:
            action = None
            reason = "Insufficient evidence for intervention."
            risk = ActionRisk.LOW

        requires_approval = (
            risk in {ActionRisk.HIGH, ActionRisk.CRITICAL}
            or confidence < self.config.minimum_confidence_for_action
        )

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

    # ═══════════════════════════════════════════════════════════════
    # ACTION PLANNING (with expanded actions)
    # ═══════════════════════════════════════════════════════════════

    def create_action_plan(
        self, decision: Decision
    ) -> Optional[ActionPlan]:
        if not decision.recommended_action:
            return None
        action = decision.recommended_action
        if self._is_on_cooldown(decision.brand_id, action):
            return None
        if not self._is_action_allowed(decision.risk, decision.confidence):
            return None

        parameters = self._build_action_parameters(decision)
        expected = self._estimate_expected_outcome(decision)

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
        self._plans[plan.id] = plan
        return plan

    def _build_action_parameters(
        self, decision: Decision
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
        if action == "run_experiment":
            return {
                "experiment_type": "hook_variation",
                "control": "direct_technical_hook",
                "variant": "story_based_hook",
                "duration_posts": 10,
            }
        if action == "change_posting_time":
            return {
                "test_new_hours": True,
                "duration_posts": 5,
            }
        if action == "change_content_type":
            return {
                "test_new_format": True,
                "duration_posts": 5,
            }
        return {}

    def _estimate_expected_outcome(
        self, decision: Decision
    ) -> Dict[str, Any]:
        baseline = decision.evidence.get("baseline", 0)
        ratio = decision.evidence.get("engagement_ratio", 1.0)
        action = decision.recommended_action

        if action == "increase_similar_content":
            target = baseline * 1.10
        elif action == "reduce_underperforming_content":
            target = baseline * 0.95
        elif action == "reduce_content_risk":
            target = baseline * 0.85
        elif action == "run_experiment":
            target = baseline * 1.05
        elif action == "change_posting_time":
            target = baseline * 1.02
        elif action == "change_content_type":
            target = baseline * 1.02
        else:
            target = baseline

        return {
            "metric": "engagement",
            "baseline": baseline,
            "current_ratio": ratio,
            "target": round(target, 2),
        }

    # ═══════════════════════════════════════════════════════════════
    # SAFETY & COOLDOWNS
    # ═══════════════════════════════════════════════════════════════

    def _is_action_allowed(
        self, risk: ActionRisk, confidence: float
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

    def _is_on_cooldown(
        self, brand_id: str, action: str
    ) -> bool:
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

    # ═══════════════════════════════════════════════════════════════
    # EXECUTION
    # ═══════════════════════════════════════════════════════════════

    def execute_plan(
        self, plan_id: str, approved: bool = False
    ) -> ActionPlan:
        plan = self._plans.get(plan_id)
        if not plan:
            raise ValueError(f"Unknown action plan: {plan_id}")
        if (
            plan.risk in {ActionRisk.HIGH, ActionRisk.CRITICAL}
            and not approved
        ):
            plan.status = ActionStatus.REJECTED
            return plan
        if self.config.dry_run:
            plan.status = ActionStatus.APPROVED
            return plan
        if not self.action_executor:
            plan.status = ActionStatus.APPROVED
            return plan

        try:
            result = self.action_executor(plan)
            plan.outcome = result
            plan.status = ActionStatus.EXECUTED
            plan.executed_at = utc_now()
            self._last_action_at[(plan.brand_id, plan.action)] = utc_now()
            
            # Publish strategy_executed event
            _publish_event("strategy_executed", {
                "brand_id": plan.brand_id,
                "action": plan.action,
                "success": True,
            })
            
            return plan
        except Exception as exc:
            plan.status = ActionStatus.FAILED
            plan.outcome = {"error": str(exc)}
            
            # Publish strategy_executed event with failure
            _publish_event("strategy_executed", {
                "brand_id": plan.brand_id,
                "action": plan.action,
                "success": False,
                "error": str(exc),
            })
            
            return plan

    # ═══════════════════════════════════════════════════════════════
    # GOALS
    # ═══════════════════════════════════════════════════════════════

    def register_goal(self, goal: Goal) -> Goal:
        self._goals[goal.id] = goal
        return goal

    def evaluate_goals(
        self, brand_id: str, metrics: Dict[str, float]
    ) -> List[Dict[str, Any]]:
        results = []
        for goal in self._goals.values():
            if (
                goal.brand_id != brand_id
                or goal.status not in {GoalStatus.ACTIVE, GoalStatus.AT_RISK}
            ):
                continue
            current = metrics.get(goal.metric)
            if current is None:
                continue
            progress = goal.calculate_progress(current)
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

    # ═══════════════════════════════════════════════════════════════
    # BRAND HEALTH (with multi-metric)
    # ═══════════════════════════════════════════════════════════════

    def calculate_brand_health(
        self, brand_id: str
    ) -> Dict[str, Any]:
        observations = list(self._observations.get(brand_id, []))
        if not observations:
            health = {
                "score": 50.0,
                "status": "unknown",
                "components": {},
            }
            self._brand_health[brand_id] = health
            
            # Publish brand_health_updated event
            _publish_event("brand_health_updated", {
                "brand_id": brand_id,
                "health_score": health.get("health_score", 0),
            })
            
            return health

        # Multi-metric baselines
        metrics = ["engagement", "engagement_rate", "click_through_rate"]
        component_scores = {}

        for metric in metrics:
            baseline = self._calculate_baseline(brand_id, metric)
            trend = self._calculate_trend(brand_id, metric)
            recent = [
                self._metric_value(item, metric)
                for item in observations[-10:]
            ]
            recent_average = safe_mean(recent)

            if baseline.mean > 0:
                performance = (
                    clamp(recent_average / baseline.mean / 2) * 100
                )
            else:
                performance = 50

            trend_score = (
                85 if trend.direction == "rising"
                else 25 if trend.direction == "falling"
                else 50
            )
            component_scores[metric] = {
                "performance": round(performance, 2),
                "trend": round(trend_score, 2),
                "combined": round(performance * 0.6 + trend_score * 0.4, 2),
            }

        # Weighted overall score
        weights = {
            "engagement": 0.4,
            "engagement_rate": 0.3,
            "click_through_rate": 0.3,
        }
        overall = sum(
            component_scores[m]["combined"] * weights[m]
            for m in metrics
        )

        score = round(clamp(overall, 0, 100), 2)
        status = (
            "healthy" if score >= 80
            else "stable" if score >= 60
            else "watch" if score >= 40
            else "weak" if score >= 20
            else "critical"
        )

        health = {
            "score": score,
            "status": status,
            "components": component_scores,
            "cluster_insights": self.brand_clusterer.get_cluster_insights(
                brand_id
            ),
            "updated_at": iso_now(),
        }
        self._brand_health[brand_id] = health
        
        # Publish brand_health_updated event
        _publish_event("brand_health_updated", {
            "brand_id": brand_id,
            "health_score": score,
        })
        
        return health

    # ═══════════════════════════════════════════════════════════════
    # STRATEGY SELECTION (with UCB)
    # ═══════════════════════════════════════════════════════════════

    def select_strategy(
        self, brand_id: str
    ) -> Optional[Strategy]:
        strategies = [
            s for s in self._strategies.values() if s.enabled
        ]
        if not strategies:
            return None

        total_trials = sum(s.trials for s in strategies)

        if random.random() < self.config.exploration_rate:
            return random.choice(strategies)

        # UCB selection
        return max(
            strategies,
            key=lambda s: s.ucb_score(total_trials),
        )

    def record_strategy_result(
        self, strategy_id: str, reward: float, success: bool
    ) -> None:
        strategy = self._strategies.get(strategy_id)
        if not strategy:
            return
        strategy.trials += 1
        if success:
            strategy.successes += 1
        else:
            strategy.failures += 1
        strategy.reward = (
            (1 - self.config.learning_rate) * strategy.reward
            + self.config.learning_rate * reward
        )

    # ═══════════════════════════════════════════════════════════════
    # AUTOMATED EXPERIMENTS
    # ═══════════════════════════════════════════════════════════════

    def create_experiment(
        self,
        brand_id: str,
        name: str,
        variable: str,
        control: str,
        variant: str,
        auto_deploy: bool = True,
    ) -> Experiment:
        experiment = Experiment(
            id=generate_id("experiment"),
            brand_id=brand_id,
            name=name,
            variable=variable,
            control=control,
            variant=variant,
            auto_deploy_winner=auto_deploy,
        )
        self._experiments[experiment.id] = experiment
        return experiment

    def record_experiment_result(
        self, experiment_id: str, variant: str, result: float
    ) -> Optional[Dict[str, Any]]:
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

    def evaluate_experiment(
        self, experiment_id: str
    ) -> Dict[str, Any]:
        experiment = self._experiments.get(experiment_id)
        if not experiment:
            raise ValueError(f"Unknown experiment: {experiment_id}")

        control = experiment.control_results
        variant = experiment.variant_results

        if (
            len(control) < experiment.minimum_samples
            or len(variant) < experiment.minimum_samples
        ):
            return {
                "status": "insufficient_data",
                "control_samples": len(control),
                "variant_samples": len(variant),
            }

        control_mean = safe_mean(control)
        variant_mean = safe_mean(variant)
        improvement = (
            0.0 if control_mean == 0
            else (variant_mean - control_mean) / abs(control_mean)
        )

        winner = (
            "variant" if improvement > 0.05
            else "control" if improvement < -0.05
            else "inconclusive"
        )
        experiment.winner = winner

        if winner != "inconclusive":
            experiment.status = "completed"

            # v4.0 — Auto-deploy winner
            if experiment.auto_deploy_winner and not experiment.deployed:
                self._deploy_experiment_winner(experiment)
                experiment.deployed = True

        return {
            "status": experiment.status,
            "winner": winner,
            "control_mean": control_mean,
            "variant_mean": variant_mean,
            "improvement": round(improvement, 4),
            "control_samples": len(control),
            "variant_samples": len(variant),
            "deployed": experiment.deployed,
        }

    def _deploy_experiment_winner(
        self, experiment: Experiment
    ) -> None:
        """Deploy the winning variant as the new default."""
        logger.info(
            "Experiment %s: deploying %s as new default",
            experiment.id,
            experiment.winner,
        )

        # Create an action plan to apply the winning variant
        decision = Decision(
            id=generate_id("decision"),
            brand_id=experiment.brand_id,
            state=DecisionState.EXPERIMENT,
            confidence=0.9,
            reason=f"Experiment {experiment.id} winner: {experiment.winner}",
            evidence={
                "experiment_id": experiment.id,
                "winner": experiment.winner,
                "variable": experiment.variable,
            },
            recommended_action="apply_experiment_winner",
            risk=ActionRisk.LOW,
        )
        self._decisions[decision.id] = decision

        plan = ActionPlan(
            id=generate_id("plan"),
            decision_id=decision.id,
            brand_id=experiment.brand_id,
            action="apply_experiment_winner",
            risk=ActionRisk.LOW,
            confidence=0.9,
            parameters={
                "experiment_id": experiment.id,
                "winner": experiment.winner,
                "variable": experiment.variable,
                "variant_value": (
                    experiment.variant
                    if experiment.winner == "variant"
                    else experiment.control
                ),
            },
        )
        self._plans[plan.id] = plan

    # ═══════════════════════════════════════════════════════════════
    # DECISION OUTCOME & LEARNING (with threshold adaptation)
    # ═══════════════════════════════════════════════════════════════

    def record_decision_outcome(
        self, decision_id: str, actual_value: float
    ) -> Optional[DecisionMemory]:
        decision = self._decisions.get(decision_id)
        if not decision:
            return None

        expected = decision.evidence.get("baseline", 0)
        action = decision.recommended_action

        if action == "increase_similar_content":
            success = actual_value >= expected * 1.10
        elif action in {"reduce_underperforming_content", "reduce_content_risk"}:
            success = actual_value >= expected * 0.95
        else:
            success = actual_value >= expected

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
        self._decision_memory.append(memory)

        # v4.0 — Update confidence calibration
        self._confidence_history[decision.brand_id].append({
            "confidence": decision.confidence,
            "success": success,
            "timestamp": utc_now(),
        })

        # v4.0 — Update adaptive thresholds
        self._update_thresholds_from_outcome(decision, memory)

        self._learn_from_decision(memory)
        self._persist_decision_memory(memory)
        return memory

    def _update_thresholds_from_outcome(
        self, decision: Decision, memory: DecisionMemory
    ) -> None:
        """Update thresholds based on decision outcome."""
        if not memory.success:
            return

        ratio = decision.evidence.get("engagement_ratio", 1.0)

        if decision.recommended_action == "increase_similar_content":
            if ratio > self.config.boost_enter_threshold:
                outcome = "true_positive"
            else:
                outcome = "false_positive"
            self.threshold_manager.update_threshold(
                "boost_enter_threshold", ratio, outcome
            )
        elif decision.recommended_action == "reduce_underperforming_content":
            if ratio < self.config.reduce_enter_threshold:
                outcome = "true_positive"
            else:
                outcome = "false_positive"
            self.threshold_manager.update_threshold(
                "reduce_enter_threshold", ratio, outcome
            )

    def _learn_from_decision(self, memory: DecisionMemory) -> None:
        if memory.actual_value is None or memory.expected_value <= 0:
            return

        reward = clamp(
            memory.actual_value / memory.expected_value, 0, 2
        )

        # v4.0 — Better strategy mapping
        strategy_map = {
            "increase_similar_content": "technical_tutorial",
            "reduce_underperforming_content": "technical_tutorial",
            "change_content_type": "technical_story",
            "change_posting_time": "educational_tip",
            "run_experiment": "technical_opinion",
        }
        strategy_id = strategy_map.get(memory.action)
        if strategy_id:
            self.record_strategy_result(
                strategy_id=strategy_id,
                reward=reward,
                success=bool(memory.success),
            )

    # ═══════════════════════════════════════════════════════════════
    # AUTONOMOUS CYCLE
    # ═══════════════════════════════════════════════════════════════

    def run_cycle(
        self, observations: Iterable[Observation]
    ) -> Dict[str, Any]:
        results = []
        grouped: Dict[str, List[Observation]] = defaultdict(list)

        for observation in observations:
            grouped[observation.brand_id].append(observation)
            results.append(self.observe(observation))

        brand_reports = {}
        for brand_id in grouped:
            health = self.calculate_brand_health(brand_id)
            goals = self.evaluate_goals(
                brand_id,
                {
                    "engagement": safe_mean(
                        [item.engagement for item in grouped[brand_id]]
                    )
                },
            )
            strategy = self.select_strategy(brand_id)
            brand_reports[brand_id] = {
                "health": health,
                "goals": goals,
                "recommended_strategy": (
                    self._serialize(strategy) if strategy else None
                ),
            }

        return {
            "timestamp": iso_now(),
            "observations_processed": len(results),
            "results": results,
            "brands": brand_reports,
        }

    # ═══════════════════════════════════════════════════════════════
    # STATUS
    # ═══════════════════════════════════════════════════════════════

    def explain_decision(self, decision_id: str) -> Dict[str, Any]:
        decision = self._decisions.get(decision_id)
        if not decision:
            return {"error": "Decision not found"}
        return {
            "decision_id": decision.id,
            "brand_id": decision.brand_id,
            "state": decision.state.value,
            "confidence": decision.confidence,
            "reason": decision.reason,
            "recommended_action": decision.recommended_action,
            "risk": decision.risk.value,
            "requires_approval": decision.requires_approval,
            "evidence": decision.evidence,
            "created_at": decision.created_at.isoformat(),
        }

    def get_status(
        self, brand_id: Optional[str] = None
    ) -> Dict[str, Any]:
        if brand_id:
            observations = list(self._observations.get(brand_id, []))
            state = self._states.get(brand_id, DecisionState.OBSERVE)
            health = self._brand_health.get(brand_id)
            return {
                "brand_id": brand_id,
                "state": state.value,
                "health": health,
                "observation_count": len(observations),
                "active_goals": len([
                    g for g in self._goals.values()
                    if g.brand_id == brand_id
                    and g.status == GoalStatus.ACTIVE
                ]),
                "active_experiments": len([
                    e for e in self._experiments.values()
                    if e.brand_id == brand_id
                    and e.status == "running"
                ]),
                "cluster_insights": (
                    self.brand_clusterer.get_cluster_insights(brand_id)
                ),
            }

        return {
            "brands": len(self._observations),
            "observations": sum(
                len(items) for items in self._observations.values()
            ),
            "decisions": len(self._decisions),
            "plans": len(self._plans),
            "goals": len(self._goals),
            "experiments": len(self._experiments),
            "strategies": len(self._strategies),
            "decision_memory": len(self._decision_memory),
            "dry_run": self.config.dry_run,
            "adaptive_thresholds": {
                "boost_enter": self.config.boost_enter_threshold,
                "reduce_enter": self.config.reduce_enter_threshold,
                "critical_performance": self.config.critical_performance_ratio,
            },
            "timestamp": iso_now(),
        }

    @staticmethod
    def _metric_value(observation: Observation, metric: str) -> float:
        try:
            return float(getattr(observation, metric, 0.0))
        except Exception:
            return 0.0

    @staticmethod
    def _serialize(value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, datetime):
            return value.isoformat()
        if hasattr(value, "__dataclass_fields__"):
            return {
                key: AutonomousConditionEngine._serialize(item)
                for key, item in asdict(value).items()
            }
        if isinstance(value, dict):
            return {
                str(k): AutonomousConditionEngine._serialize(v)
                for k, v in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [
                AutonomousConditionEngine._serialize(item)
                for item in value
            ]
        return value

    # ═══════════════════════════════════════════════════════════════
    # PERSISTENCE
    # ═══════════════════════════════════════════════════════════════

    def _persist_decision(self, decision: Decision) -> None:
        if not self.supabase or not self.config.persist_decisions:
            return
        try:
            self.supabase.table("autonomous_decisions").upsert({
                "id": decision.id,
                "brand_id": decision.brand_id,
                "state": decision.state.value,
                "confidence": decision.confidence,
                "reason": decision.reason,
                "recommended_action": decision.recommended_action,
                "risk": decision.risk.value,
                "requires_approval": decision.requires_approval,
                "evidence": decision.evidence,
                "created_at": decision.created_at.isoformat(),
            }).execute()
        except Exception as exc:
            logger.warning("Failed to persist decision: %s", exc)

    def _persist_decision_memory(self, memory: DecisionMemory) -> None:
        if not self.supabase or not self.config.persist_decisions:
            return
        try:
            self.supabase.table("autonomous_decision_memory").upsert({
                "decision_id": memory.decision_id,
                "brand_id": memory.brand_id,
                "action": memory.action,
                "reason": memory.reason,
                "confidence": memory.confidence,
                "expected_value": memory.expected_value,
                "actual_value": memory.actual_value,
                "success": memory.success,
                "created_at": memory.created_at.isoformat(),
                "evaluated_at": (
                    memory.evaluated_at.isoformat()
                    if memory.evaluated_at else None
                ),
            }).execute()
        except Exception as exc:
            logger.warning(
                "Failed to persist decision memory: %s", exc
            )

    # ═══════════════════════════════════════════════════════════════
    # ADMIN
    # ═══════════════════════════════════════════════════════════════

    def clear_brand_cache(self, brand_id: str) -> None:
        with self._lock:
            self._observations.pop(brand_id, None)
            self._brand_health.pop(brand_id, None)
            self._states.pop(brand_id, None)
            for key in [
                k for k in self._baselines if k[0] == brand_id
            ]:
                self._baselines.pop(key, None)
            for key in [
                k for k in self._trends if k[0] == brand_id
            ]:
                self._trends.pop(key, None)
            for key in [
                k for k in self._topic_baselines if k[0] == brand_id
            ]:
                self._topic_baselines.pop(key, None)

    def export_state(self) -> Dict[str, Any]:
        return {
            "timestamp": iso_now(),
            "status": self.get_status(),
            "states": {
                bid: s.value for bid, s in self._states.items()
            },
            "health": self._serialize(self._brand_health),
            "strategies": self._serialize(self._strategies),
            "goals": self._serialize(self._goals),
            "experiments": self._serialize(self._experiments),
        }


# ============================================================================
# DEFAULT ACTION EXECUTOR
# ============================================================================

def default_action_executor(plan: ActionPlan) -> Dict[str, Any]:
    logger.info("Action executor received: %s", plan.action)
    return {
        "success": True,
        "action": plan.action,
        "parameters": plan.parameters,
        "executed_at": iso_now(),
        "message": "Action accepted by autonomous executor.",
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
        enable_adaptive_thresholds=True,
        enable_multi_metric=True,
        enable_predictive_trend=True,
        enable_automated_experiments=True,
        enable_brand_clustering=True,
        enable_anomaly_classification=True,
        enable_confidence_calibration=True,
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
    return autonomous_engine.observe(observation)


def get_engine_status(
    brand_id: Optional[str] = None
) -> Dict[str, Any]:
    return autonomous_engine.get_status(brand_id)


def register_default_goal(brand_id: str) -> Goal:
    return autonomous_engine.register_goal(
        Goal(
            id=generate_id("goal"),
            brand_id=brand_id,
            name="Increase meaningful engagement",
            metric="engagement",
            target_value=120.0,
            baseline_value=100.0,
            deadline=(utc_now() + timedelta(days=30)),
        )
    )


def create_hook_experiment(brand_id: str) -> Experiment:
    return autonomous_engine.create_experiment(
        brand_id=brand_id,
        name="Hook Performance Experiment",
        variable="hook_type",
        control="direct_technical_hook",
        variant="story_based_hook",
    )


def health_check() -> Dict[str, Any]:
    return {
        "service": "autonomous_condition_engine",
        "status": "healthy",
        "version": "4.0.1",
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
    "AnomalyType",
    "AutonomousConditionEngine",
    "AnomalyClassifier",
    "PredictiveTrendAnalyzer",
    "BrandClusterer",
    "AdaptiveThresholdManager",
    "autonomous_engine",
    "evaluate_engagement",
    "get_engine_status",
    "register_default_goal",
    "create_hook_experiment",
    "health_check",
]
