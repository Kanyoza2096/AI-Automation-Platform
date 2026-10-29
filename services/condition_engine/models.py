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


from .utils import utc_now, parse_datetime

class DecisionState(str, Enum):
    """Autonomous decision states."""
    OBSERVE = "observe"
    MONITOR = "monitor"
    BOOST = "boost"
    REDUCE = "reduce"
    EXPERIMENT = "experiment"
    RECOVER = "recover"
    PAUSE = "pause"
    CRITICAL = "critical"


class ActionRisk(str, Enum):
    """Risk levels for actions."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ActionStatus(str, Enum):
    """Action plan statuses."""
    PROPOSED = "proposed"
    APPROVED = "approved"
    EXECUTED = "executed"
    REJECTED = "rejected"
    FAILED = "failed"
    EXPIRED = "expired"


class GoalStatus(str, Enum):
    """Goal statuses."""
    ACTIVE = "active"
    ACHIEVED = "achieved"
    AT_RISK = "at_risk"
    FAILED = "failed"
    PAUSED = "paused"


class AnomalyType(str, Enum):
    """Anomaly classification types."""
    SPIKE = "spike"
    DIP = "dip"
    DRIFT = "drift"
    LEVEL_SHIFT = "level_shift"
    OUTLIER = "outlier"


# ═══════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class EngineConfig:
    """Configuration with adaptive thresholds."""
    
    # Baseline
    baseline_window: int = 30
    minimum_baseline_samples: int = 5
    minimum_confidence_for_action: float = 0.65
    strong_confidence: float = 0.85
    
    # Thresholds
    boost_enter_threshold: float = 1.50
    boost_exit_threshold: float = 1.10
    reduce_enter_threshold: float = 0.70
    reduce_exit_threshold: float = 0.95
    critical_performance_ratio: float = 0.45
    
    # Velocity
    strong_positive_velocity: float = 0.15
    strong_negative_velocity: float = -0.15
    
    # Anomaly
    anomaly_z_score: float = 3.0
    minimum_anomaly_samples: int = 8
    
    # Cooldowns (minutes)
    default_action_cooldown_minutes: int = 360
    boost_cooldown_minutes: int = 720
    reduce_cooldown_minutes: int = 720
    experiment_cooldown_minutes: int = 1440
    
    # Autonomy levels
    allow_low_risk_autonomy: bool = True
    allow_medium_risk_autonomy: bool = True
    allow_high_risk_autonomy: bool = False
    allow_critical_autonomy: bool = False
    
    # Learning
    learning_rate: float = 0.20
    exploration_rate: float = 0.15
    goal_success_tolerance: float = 0.05
    
    # Execution
    dry_run: bool = False
    persist_decisions: bool = True
    
    # v5.0 — Enhanced features
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
    
    # v5.1 — Enhanced learning
    enable_context_learning: bool = True
    enable_time_patterns: bool = True
    enable_causal_chains: bool = True
    enable_decay_weighting: bool = True
    decay_days: int = 7
    min_context_samples: int = 3
    min_time_samples: int = 3
    unreliable_context_threshold: float = 0.5
    unreliable_time_threshold: float = 0.4
    critical_skip_threshold: float = 0.2
    
    # v5.0 — Bounded memory
    max_observations_per_brand: int = 500
    max_decisions: int = 1000
    max_plans: int = 500
    max_decision_memory: int = 500
    max_experiments: int = 50
    max_context_entries: int = 2000
    max_time_entries: int = 2000
    max_causal_entries: int = 1000


# ═══════════════════════════════════════════════════════════════════════════
# DATA MODELS
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class Observation:
    """Engagement observation for a post."""
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
    correlation_id: str = field(default_factory=lambda: hashlib.sha256(str(time.time_ns()).encode()).hexdigest()[:16])
    
    def normalize(self) -> "Observation":
        """Normalize observation values."""
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
    """Statistical baseline for a metric."""
    metric: str
    mean: float = 0.0
    median: float = 0.0
    stddev: float = 0.0
    minimum: float = 0.0
    maximum: float = 0.0
    sample_count: int = 0
    calculated_at: datetime = field(default_factory=utc_now)
    
    def ratio(self, value: float) -> float:
        """Calculate ratio to baseline mean."""
        if self.mean <= 0:
            return 1.0
        return value / self.mean
    
    def z_score(self, value: float) -> float:
        """Calculate z-score."""
        if self.stddev <= 0:
            return 0.0
        return (value - self.mean) / self.stddev


@dataclass
class Trend:
    """Trend analysis with prediction."""
    metric: str
    velocity: float = 0.0
    acceleration: float = 0.0
    direction: str = "stable"
    confidence: float = 0.0
    sample_count: int = 0
    predicted_next_value: float = 0.0
    predicted_direction: str = "stable"
    reversal_probability: float = 0.0
    time_to_baseline_recovery: Optional[float] = None


@dataclass
class Anomaly:
    """Detected anomaly with classification."""
    metric: str
    value: float
    expected: float
    z_score: float
    severity: str
    confidence: float
    detected_at: datetime = field(default_factory=utc_now)
    anomaly_type: AnomalyType = AnomalyType.OUTLIER
    root_cause_hypothesis: str = ""


@dataclass
class Goal:
    """Business goal."""
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
        """Calculate goal progress."""
        delta = self.target_value - self.baseline_value
        if delta == 0:
            self.progress = 1.0
            return self.progress
        self.progress = max(0.0, min(1.0, (current_value - self.baseline_value) / delta))
        return self.progress


@dataclass
class Strategy:
    """Content strategy with UCB scoring (enhanced v5.1)."""
    id: str
    name: str
    description: str = ""
    reward: float = 0.0
    trials: int = 0
    successes: int = 0
    failures: int = 0
    enabled: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    # v5.1 — Context-aware tracking
    context_stats: Dict[str, Dict[str, int]] = field(default_factory=lambda: defaultdict(lambda: {"successes": 0, "failures": 0}))
    time_stats: Dict[str, Dict[str, int]] = field(default_factory=lambda: defaultdict(lambda: {"successes": 0, "failures": 0}))
    recent_outcomes: Deque[Dict[str, Any]] = field(default_factory=lambda: deque(maxlen=100))
    total_duration_ms: float = 0.0
    
    @property
    def success_rate(self) -> float:
        if self.trials <= 0:
            return 0.5
        return self.successes / self.trials
    
    def ucb_score(self, total_trials: int) -> float:
        """Upper Confidence Bound score."""
        if self.trials <= 0:
            return float('inf')
        exploration = math.sqrt(2 * math.log(total_trials + 1) / self.trials)
        return self.success_rate + exploration
    
    def context_success_rate(self, context_key: str) -> Optional[float]:
        """Success rate for a specific context."""
        stats = self.context_stats.get(context_key)
        if not stats:
            return None
        total = stats["successes"] + stats["failures"]
        if total < 3:
            return None
        return stats["successes"] / total
    
    def time_success_rate(self, time_bucket: str) -> Optional[float]:
        """Success rate for a time-of-day bucket."""
        stats = self.time_stats.get(time_bucket)
        if not stats:
            return None
        total = stats["successes"] + stats["failures"]
        if total < 3:
            return None
        return stats["successes"] / total
    
    def recent_success_rate(self, days: int = 7) -> Optional[float]:
        """Success rate for recent outcomes."""
        cutoff = utc_now() - timedelta(days=days)
        recent = [o for o in self.recent_outcomes if o.get("timestamp") and parse_datetime(o["timestamp"]) and parse_datetime(o["timestamp"]) >= cutoff]
        if not recent:
            return None
        successes = sum(1 for o in recent if o.get("success"))
        return successes / len(recent)
    
    def avg_duration_ms(self) -> Optional[float]:
        """Average duration if we have samples."""
        if not self.recent_outcomes:
            return None
        durations = [o.get("duration_ms", 0) for o in self.recent_outcomes if o.get("duration_ms")]
        if not durations:
            return None
        return sum(durations) / len(durations)


@dataclass
class Decision:
    """Autonomous decision."""
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
    correlation_id: str = field(default_factory=lambda: hashlib.sha256(str(time.time_ns()).encode()).hexdigest()[:16])
    context_key: str = ""  # v5.1
    time_bucket: str = ""  # v5.1


@dataclass
class ActionPlan:
    """Action plan for execution."""
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
    """A/B experiment."""
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
    auto_deploy_winner: bool = True
    deployed: bool = False


@dataclass
class DecisionMemory:
    """Record of decision outcomes for learning."""
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


# ═══════════════════════════════════════════════════════════════════════════
# UTILITIES
# ═══════════════════════════════════════════════════════════════════════════
