"""
Autonomous Loop v4.0 — GENUINE OODA (Boyd-compliant)
======================================================

v4.0 ADDITIONS (Additive only — nothing removed):
  - WorldModel: persistent expectations of system behavior
  - Deviation detection: observations vs expectations
  - Model updates: loop changes its own beliefs
  - Adaptive loop frequency: volatility-aware tick rate
  - Orientation-driven observation: focus shifts as model evolves
  - Implicit guidance: skip deliberation when confident
  - Temporal attention: weighted observation based on current model

v3.4 (Retained):
  - Context-aware intelligence (bandit + Bayesian + seasonal)
  - Verified outcome feedback
  - Causal chain introspection
  - All v3.3 features preserved

v3.3 (Retained):
  - Multi-Armed Bandit, Bayesian, Seasonal, Adaptive Thresholds, Smoothing
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional, Protocol, runtime_checkable
from statistics import mean, median, stdev

# HealthReport and HealthStatus are canonical in lifecycle. They were
# previously redefined in this module as local classes, which made
# isinstance(report, HealthReport) fail inside
# ServiceHealthRegistry._normalize_health_result — the loop's reports
# were classified as "unrecognized health payload" and surfaced as
# status=unknown in /health/detailed.
#
# lifecycle.py imports core.autonomous_loop only inside
# _boot_autonomous_loop (a lazy function-scope import), so no
# module-load cycle exists.
from lifecycle import HealthReport, HealthStatus

logger = logging.getLogger("kanyoza.autonomous_loop")


# ═══════════════════════════════════════════════════════════════════════════
# ENUMS & DATACLASSES
# ═══════════════════════════════════════════════════════════════════════════

class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class DecisionType(Enum):
    OBSERVE = "observe"
    ORIENT = "orient"
    DECIDE = "decide"
    ACT = "act"
    LEARN = "learn"
    ADAPT = "adapt"


class GuidanceMode(Enum):
    """Boyd's implicit vs explicit guidance distinction.

    EXPLICIT:      Deliberation required — state is unfamiliar or the
                   bandit's confidence is low. The loop reasons about
                   the action before committing.
    IMPLICIT:      High confidence + low deviation — pattern seen before,
                   action is habitual. Skip deliberation.
    OBSERVE_ONLY:  Low confidence + high deviation — situation is novel,
                   no signal to act on. Observe without acting.
    """
    EXPLICIT = "explicit"
    IMPLICIT = "implicit"
    OBSERVE_ONLY = "observe_only"


@dataclass
class Memory:
    event_id: str
    timestamp: datetime
    event_type: str
    payload: Dict[str, Any]
    outcome: Optional[str] = None
    success: Optional[bool] = None
    learned_insight: Optional[str] = None
    correlation_id: Optional[str] = None


@dataclass
class Pattern:
    id: str
    name: str
    description: str
    conditions: Dict[str, Any]
    recommended_action: str
    confidence: float
    occurrences: int = 0
    success_count: int = 0
    last_occurrence: Optional[datetime] = None


@dataclass
class Prediction:
    service: str
    predicted_issue: str
    probability: float
    estimated_time_minutes: float
    confidence: float
    recommended_action: str


@dataclass
class IntelligenceDecision:
    decision_id: str
    type: DecisionType
    action: str
    rationale: str
    confidence: float
    alternatives: List[str]
    selected_alternative: str
    timestamp: datetime
    state: str = ""
    guidance_mode: GuidanceMode = GuidanceMode.EXPLICIT  # v4.0


@dataclass
class LoopMetrics:
    events_processed: int = 0
    events_failed: int = 0
    events_by_type: dict[str, int] = field(default_factory=dict)
    decisions_made: int = 0
    decisions_successful: int = 0
    patterns_learned: int = 0
    predictions_made: int = 0
    predictions_correct: int = 0
    proactive_healing_attempts: int = 0
    proactive_healing_successes: int = 0
    handler_latency_ms: dict[str, float] = field(default_factory=dict)
    circuit_breaker_states: dict[str, str] = field(default_factory=dict)
    intelligence_confidence: float = 0.0
    learning_rate: float = 0.0
    bandit_explorations: int = 0
    bandit_exploitations: int = 0
    bayesian_updates: int = 0
    verified_outcomes_learned: int = 0
    # v4.0
    world_model_updates: int = 0
    deviations_detected: int = 0
    implicit_decisions: int = 0
    explicit_decisions: int = 0
    adaptive_speedups: int = 0
    orientation_reshapes: int = 0


# ═══════════════════════════════════════════════════════════════════════════
# CIRCUIT BREAKER
# ═══════════════════════════════════════════════════════════════════════════

class CircuitBreaker:
    def __init__(self, name: str, failure_threshold: int = 5, recovery_timeout: float = 30.0):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.state = CircuitState.CLOSED
        self._failures = 0
        self._successes = 0
        self._last_failure_time: Optional[float] = None
        self._lock = asyncio.Lock()

    async def execute(self, func: Callable[[], Awaitable[Any]]) -> Any:
        async with self._lock:
            if self.state == CircuitState.OPEN:
                if self._should_retry():
                    self.state = CircuitState.HALF_OPEN
                    logger.info(f"🔓 Circuit breaker '{self.name}' half-open")
                else:
                    raise CircuitBreakerOpenError(self.name)

        try:
            result = await func()
            await self._record_success()
            return result
        except Exception as exc:
            await self._record_failure()
            raise

    async def _record_success(self) -> None:
        async with self._lock:
            self._successes += 1
            self._failures = max(0, self._failures - 1)
            if self.state == CircuitState.HALF_OPEN and self._successes >= 3:
                self.state = CircuitState.CLOSED
                logger.info(f"✅ Circuit breaker '{self.name}' closed")

    async def _record_failure(self) -> None:
        async with self._lock:
            self._failures += 1
            self._last_failure_time = time.monotonic()
            if self._failures >= self.failure_threshold:
                self.state = CircuitState.OPEN
                logger.warning(f"🚨 Circuit breaker '{self.name}' opened after {self._failures} failures")

    def _should_retry(self) -> bool:
        if self._last_failure_time is None:
            return True
        return time.monotonic() - self._last_failure_time >= self.recovery_timeout

    def get_stats(self) -> Dict[str, Any]:
        return {
            "state": self.state.value,
            "failures": self._failures,
            "successes": self._successes,
            "is_open": self.state == CircuitState.OPEN,
        }


class CircuitBreakerOpenError(Exception):
    def __init__(self, name: str):
        self.name = name
        super().__init__(f"Circuit breaker '{self.name}' is OPEN")


# ═══════════════════════════════════════════════════════════════════════════
# WORLD MODEL (v4.0 — Boyd's schwerpunkt)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class Expectation:
    """What we expect about a specific signal."""
    signal_key: str  # e.g. "engine:redis|status:degraded"
    expected_frequency: float = 0.0  # events per hour
    expected_success_rate: float = 0.5
    samples: int = 0
    last_observed: Optional[datetime] = None
    volatility: float = 0.0  # how much this signal varies

    def update(self, observed: bool, time_delta_hours: float) -> None:
        """Update expectation based on observation."""
        self.samples += 1
        # Exponential moving average of frequency
        if time_delta_hours > 0:
            observed_freq = (1.0 if observed else 0.0) / max(time_delta_hours, 0.001)
            alpha = 0.2
            self.expected_frequency = (
                self.expected_frequency * (1 - alpha) + observed_freq * alpha
            )
        self.last_observed = datetime.now(timezone.utc)


class WorldModel:
    """
    Persistent expectations of how the system should behave.

    Boyd's key insight: Orientation means comparing raw observations
    against a mental model of "normal" — deviation from expectation is
    what makes a signal meaningful.

    This is what was missing from v3.4.
    """

    def __init__(self, max_expectations: int = 500):
        self.max_expectations = max_expectations
        self._expectations: Dict[str, Expectation] = {}
        self._signal_counts: Dict[str, int] = defaultdict(int)
        self._total_observations = 0
        self._model_age_seconds = 0.0
        self._last_update = time.time()
        self._lock = asyncio.Lock()

        # Volatility tracking for adaptive frequency
        self._recent_signal_rate: deque = deque(maxlen=60)  # last 60 ticks
        self._baseline_signal_rate: float = 0.0

    async def observe_signal(self, signal_key: str, success: bool = True) -> float:
        """
        Record that a signal was observed. Returns deviation score (0-1).

        0 = totally expected
        1 = totally unexpected
        """
        async with self._lock:
            now = time.time()
            time_delta_hours = (now - self._last_update) / 3600.0
            self._last_update = now
            self._model_age_seconds += time_delta_hours * 3600

            exp = self._expectations.get(signal_key)
            if exp is None:
                # First time seeing this signal — brand new, but not alarming
                exp = Expectation(signal_key=signal_key)
                self._expectations[signal_key] = exp
                if len(self._expectations) > self.max_expectations:
                    # Evict oldest
                    oldest = min(
                        self._expectations.items(),
                        key=lambda x: x[1].last_observed or datetime.min.replace(tzinfo=timezone.utc)
                    )
                    del self._expectations[oldest[0]]

            # Compute deviation BEFORE updating
            if exp.samples < 3:
                deviation = 0.5  # Not enough history — neutral deviation
            else:
                expected_rate = exp.expected_frequency
                observed_rate = 1.0 / max(time_delta_hours, 0.001)
                if expected_rate <= 0:
                    deviation = min(1.0, observed_rate / 100.0)
                else:
                    deviation = min(1.0, abs(observed_rate - expected_rate) / max(expected_rate, 0.001))

            # Update expectation
            exp.update(observed=True, time_delta_hours=time_delta_hours)
            exp.expected_success_rate = (
                exp.expected_success_rate * (exp.samples - 1) + (1.0 if success else 0.0)
            ) / exp.samples

            self._signal_counts[signal_key] += 1
            self._total_observations += 1
            self._recent_signal_rate.append(1)

            return deviation

    async def observe_idle(self) -> None:
        """Record that a tick happened with no signals (for frequency baseline)."""
        async with self._lock:
            self._recent_signal_rate.append(0)

    def compute_volatility(self) -> float:
        """
        How volatile is the current signal environment?

        0 = quiet, all ticks same
        1 = chaotic, wildly varying
        """
        if len(self._recent_signal_rate) < 10:
            return 0.0

        rates = list(self._recent_signal_rate)
        avg = sum(rates) / len(rates)
        if avg == 0:
            return 0.0

        # Variance-based volatility
        variance = sum((r - avg) ** 2 for r in rates) / len(rates)
        std = math.sqrt(variance)

        # Normalize: std 0 = quiet, std 1 = very volatile
        return min(1.0, std / max(avg, 0.1))

    async def detect_deviation(self, signal_key: str) -> float:
        """Check current deviation for a signal without updating."""
        async with self._lock:
            exp = self._expectations.get(signal_key)
            if not exp or exp.samples < 3:
                return 0.5
            # Simple heuristic: if we see this signal much more than expected, high deviation
            return min(1.0, self._signal_counts[signal_key] / max(exp.expected_frequency * 10, 5))

    def get_expectations(self) -> Dict[str, Dict[str, Any]]:
        """Introspection: what do we expect?"""
        return {
            key: {
                "expected_frequency_per_hour": round(exp.expected_frequency, 4),
                "expected_success_rate": round(exp.expected_success_rate, 3),
                "samples": exp.samples,
                "volatility": round(exp.volatility, 3),
            }
            for key, exp in self._expectations.items()
        }

    def get_top_expectations(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Most active expectations."""
        sorted_exps = sorted(
            self._expectations.values(),
            key=lambda e: e.samples,
            reverse=True,
        )[:limit]
        return [
            {
                "signal": e.signal_key,
                "frequency": round(e.expected_frequency, 4),
                "success_rate": round(e.expected_success_rate, 3),
                "samples": e.samples,
            }
            for e in sorted_exps
        ]

    def get_status(self) -> Dict[str, Any]:
        return {
            "expectations_learned": len(self._expectations),
            "total_observations": self._total_observations,
            "model_age_hours": round(self._model_age_seconds / 3600, 2),
            "current_volatility": round(self.compute_volatility(), 3),
        }


# ═══════════════════════════════════════════════════════════════════════════
# MULTI-ARMED BANDIT (Retained from v3.4)
# ═══════════════════════════════════════════════════════════════════════════

class MultiArmedBandit:
    def __init__(self, epsilon: float = 0.15, learning_rate: float = 0.1):
        self.epsilon = epsilon
        self.learning_rate = learning_rate
        self._state_actions: Dict[str, Dict[str, Dict[str, float]]] = defaultdict(
            lambda: defaultdict(lambda: {"attempts": 0, "successes": 0, "reward_sum": 0.0, "avg_reward": 0.5})
        )
        self._explorations = 0
        self._exploitations = 0

    def choose_action(self, state: str, available_actions: List[str]) -> str:
        if not available_actions:
            return "monitor"
        if random.random() < self.epsilon:
            self._explorations += 1
            return random.choice(available_actions)
        self._exploitations += 1
        best_action = available_actions[0]
        best_reward = -1.0
        for action in available_actions:
            stats = self._state_actions[state][action]
            if stats["avg_reward"] > best_reward:
                best_reward = stats["avg_reward"]
                best_action = action
        return best_action

    def update(self, state: str, action: str, reward: float) -> None:
        stats = self._state_actions[state][action]
        stats["attempts"] += 1
        if reward > 0.5:
            stats["successes"] += 1
        stats["avg_reward"] = (
            stats["avg_reward"] * (1 - self.learning_rate) +
            reward * self.learning_rate
        )
        stats["reward_sum"] += reward

    def get_action_stats(self, state: str) -> Dict[str, Dict[str, float]]:
        return dict(self._state_actions.get(state, {}))

    def get_best_action(self, state: str) -> Optional[str]:
        actions = self._state_actions.get(state)
        if not actions:
            return None
        return max(actions, key=lambda a: actions[a].get("avg_reward", 0.5))

    def get_best_confidence(self, state: str) -> float:
        """v4.0 — How confident are we in the best action for this state?"""
        actions = self._state_actions.get(state)
        if not actions:
            return 0.0
        best_stats = max(actions.values(), key=lambda s: s.get("avg_reward", 0.5))
        attempts = best_stats.get("attempts", 0)
        reward = best_stats.get("avg_reward", 0.5)
        if attempts < 3:
            return 0.0
        # Confidence = reward scaled by sample size (caps at 20 samples)
        sample_factor = min(1.0, attempts / 20.0)
        return reward * sample_factor

    def get_stats(self) -> Dict[str, Any]:
        total_states = len(self._state_actions)
        total_actions = sum(len(actions) for actions in self._state_actions.values())
        return {
            "states_learned": total_states,
            "actions_learned": total_actions,
            "explorations": self._explorations,
            "exploitations": self._exploitations,
            "exploitation_rate": (
                self._exploitations / max(1, self._exploitations + self._explorations)
            ),
        }


# ═══════════════════════════════════════════════════════════════════════════
# BAYESIAN BELIEF NETWORK (Retained)
# ═══════════════════════════════════════════════════════════════════════════

class BayesianNetwork:
    def __init__(self):
        self._conditional_counts: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._cause_totals: Dict[str, int] = defaultdict(int)
        self._updates = 0

    def observe(self, cause: str, effect: str) -> None:
        self._conditional_counts[cause][effect] += 1
        self._cause_totals[cause] += 1
        self._updates += 1

    def probability(self, effect: str, cause: str) -> float:
        if self._cause_totals.get(cause, 0) == 0:
            return 0.0
        return self._conditional_counts[cause].get(effect, 0) / self._cause_totals[cause]

    def most_likely_cause(self, effect: str, candidates: List[str]) -> Optional[str]:
        scores = {}
        for candidate in candidates:
            prob = self.probability(effect, candidate)
            if prob > 0:
                scores[candidate] = prob
        if not scores:
            return None
        return max(scores, key=scores.get)

    def get_causal_chain(self, start: str, max_depth: int = 3) -> List[str]:
        chain = [start]
        current = start
        for _ in range(max_depth):
            effects = self._conditional_counts.get(current, {})
            if not effects:
                break
            most_likely = max(effects, key=effects.get)
            if most_likely in chain:
                break
            chain.append(most_likely)
            current = most_likely
        return chain

    def get_effects_for_cause(self, cause: str) -> Dict[str, int]:
        return dict(self._conditional_counts.get(cause, {}))

    def get_causes_for_effect(self, effect: str) -> Dict[str, float]:
        result = {}
        for cause, effects in self._conditional_counts.items():
            if effect in effects:
                total = self._cause_totals[cause]
                if total > 0:
                    result[cause] = effects[effect] / total
        return result

    def get_stats(self) -> Dict[str, Any]:
        return {
            "causes_learned": len(self._cause_totals),
            "conditional_relationships": self._updates,
            "top_causes": sorted(
                self._cause_totals.items(),
                key=lambda x: x[1],
                reverse=True,
            )[:5],
        }


# ═══════════════════════════════════════════════════════════════════════════
# SEASONAL ANOMALY DETECTOR (Retained)
# ═══════════════════════════════════════════════════════════════════════════

class SeasonalAnomalyDetector:
    def __init__(self, min_samples: int = 10, z_threshold: float = 3.0):
        self.min_samples = min_samples
        self.z_threshold = z_threshold
        self._seasonal_history: Dict[str, Dict[str, List[float]]] = defaultdict(
            lambda: defaultdict(list)
        )
        self._seasonal_stats: Dict[str, Dict[str, Dict[str, float]]] = defaultdict(dict)

    def _get_season_key(self) -> str:
        now = datetime.now(timezone.utc)
        return f"{now.weekday()}:{now.hour}"

    def record(self, metric: str, value: float) -> None:
        season = self._get_season_key()
        self._seasonal_history[metric][season].append(value)
        if len(self._seasonal_history[metric][season]) > 100:
            self._seasonal_history[metric][season] = self._seasonal_history[metric][season][-100:]
        values = self._seasonal_history[metric][season]
        if len(values) >= self.min_samples:
            mean_val = sum(values) / len(values)
            std_val = (sum((v - mean_val)**2 for v in values) / len(values)) ** 0.5
            self._seasonal_stats[metric][season] = {
                "mean": mean_val,
                "std": std_val,
                "samples": len(values),
            }

    def is_anomaly(self, metric: str, value: float) -> bool:
        season = self._get_season_key()
        stats = self._seasonal_stats.get(metric, {}).get(season)
        if not stats or stats["std"] == 0:
            return False
        z_score = abs(value - stats["mean"]) / stats["std"]
        return z_score >= self.z_threshold

    def get_z_score(self, metric: str, value: float) -> float:
        season = self._get_season_key()
        stats = self._seasonal_stats.get(metric, {}).get(season)
        if not stats or stats["std"] == 0:
            return 0.0
        return (value - stats["mean"]) / stats["std"]

    def get_stats(self) -> Dict[str, Any]:
        return {
            "metrics_tracked": len(self._seasonal_history),
            "seasons_learned": sum(len(s) for s in self._seasonal_history.values()),
            "threshold": self.z_threshold,
        }


# ═══════════════════════════════════════════════════════════════════════════
# ADAPTIVE THRESHOLDS (Retained)
# ═══════════════════════════════════════════════════════════════════════════

class AdaptiveThresholds:
    def __init__(self, default_threshold: float = 50.0, min_samples: int = 10):
        self.default_threshold = default_threshold
        self.min_samples = min_samples
        self._history: Dict[str, List[float]] = defaultdict(list)
        self._thresholds: Dict[str, float] = {}

    def record(self, metric: str, value: float) -> None:
        self._history[metric].append(value)
        if len(self._history[metric]) > 200:
            self._history[metric] = self._history[metric][-200:]
        values = self._history[metric]
        if len(values) >= self.min_samples:
            mean_val = sum(values) / len(values)
            std_val = (sum((v - mean_val)**2 for v in values) / len(values)) ** 0.5
            self._thresholds[metric] = mean_val + 3 * std_val

    def get_threshold(self, metric: str) -> float:
        return self._thresholds.get(metric, self.default_threshold)

    def is_exceeded(self, metric: str, value: float) -> bool:
        return value > self.get_threshold(metric)

    def get_stats(self) -> Dict[str, Any]:
        return {
            "thresholds_learned": len(self._thresholds),
            "metrics_tracked": len(self._history),
            "thresholds": dict(self._thresholds),
        }


# ═══════════════════════════════════════════════════════════════════════════
# EXPONENTIAL SMOOTHING PREDICTOR (Retained)
# ═══════════════════════════════════════════════════════════════════════════

class ExponentialSmoothingPredictor:
    def __init__(self, alpha: float = 0.3, beta: float = 0.1):
        self.alpha = alpha
        self.beta = beta
        self._levels: Dict[str, float] = {}
        self._trends: Dict[str, float] = {}

    def update(self, key: str, value: float) -> None:
        if key not in self._levels:
            self._levels[key] = value
            self._trends[key] = 0.0
            return
        old_level = self._levels[key]
        old_trend = self._trends[key]
        self._levels[key] = self.alpha * value + (1 - self.alpha) * (old_level + old_trend)
        self._trends[key] = self.beta * (self._levels[key] - old_level) + (1 - self.beta) * old_trend

    def predict(self, key: str, steps_ahead: int = 1) -> Optional[float]:
        if key not in self._levels:
            return None
        return self._levels[key] + steps_ahead * self._trends[key]

    def get_trend(self, key: str) -> float:
        return self._trends.get(key, 0.0)

    def get_stats(self) -> Dict[str, Any]:
        return {
            "models_tracked": len(self._levels),
            "positive_trends": sum(1 for t in self._trends.values() if t > 0),
            "negative_trends": sum(1 for t in self._trends.values() if t < 0),
        }


# ═══════════════════════════════════════════════════════════════════════════
# INTELLIGENCE ENGINE (v4.0 — integrates WorldModel)
# ═══════════════════════════════════════════════════════════════════════════

class IntelligenceEngine:
    """
    Enhanced intelligence with Boyd-compliant Orientation.

    Now includes:
    - WorldModel (expectations of normal behavior)
    - Deviation detection
    - Implicit vs explicit guidance decisions
    """

    def __init__(self, ai_provider=None):
        self.ai = ai_provider
        self._patterns: Dict[str, Pattern] = {}
        self._decision_history: deque = deque(maxlen=1000)
        self._learning_rate = 0.1
        self._confidence = 0.5

        # ML components
        self.bandit = MultiArmedBandit()
        self.bayesian = BayesianNetwork()
        self.seasonal_detector = SeasonalAnomalyDetector()
        self.adaptive_thresholds = AdaptiveThresholds()
        self.predictor = ExponentialSmoothingPredictor()

        # v4.0 — Boyd's schwerpunkt
        self.world_model = WorldModel()

    def signal_key_for_event(self, event: Dict[str, Any]) -> str:
        """v4.0 — canonical key for world model."""
        event_type = event.get("event_type", "unknown")
        payload = event.get("payload", {})
        engine = payload.get("engine", "none") if isinstance(payload, dict) else "none"
        status = payload.get("status", "none") if isinstance(payload, dict) else "none"
        return f"{event_type}|{engine}|{status}"

    async def analyze_context(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """v4.0 — Orientation. Compares observation vs world model."""
        # v4.0 — get deviation from world model
        signal_key = self.signal_key_for_event(event)
        deviation = await self.world_model.observe_signal(signal_key, success=True)

        context = {
            "event_type": event.get("event_type", "unknown"),
            "timestamp": event.get("timestamp", datetime.now(timezone.utc).isoformat()),
            "correlation_id": event.get("correlation_id", uuid.uuid4().hex),
            "previous_decisions": list(self._decision_history)[-5:],
            "patterns_matched": [],
            "confidence": self._confidence,
            # v4.0
            "signal_key": signal_key,
            "deviation_from_expectation": deviation,
            "is_novel": deviation > 0.7,
        }

        for pattern in self._patterns.values():
            if self._pattern_matches(pattern, event):
                context["patterns_matched"].append({
                    "id": pattern.id,
                    "name": pattern.name,
                    "confidence": pattern.confidence,
                })

        return context

    async def generate_decision(self, event: Dict[str, Any], context: Dict[str, Any]) -> IntelligenceDecision:
        """v4.0 — Boyd's implicit vs explicit guidance."""
        decision_type = self._classify_event(event)

        engine = event.get("engine", event.get("payload", {}).get("engine", "unknown"))
        status = event.get("status", event.get("payload", {}).get("status", "unknown"))
        state = f"{engine}|{status}|{datetime.now(timezone.utc).strftime('%H')}"

        available_actions = self._get_available_actions(decision_type, event)

        # v4.0 — Boyd: if we're confident, use implicit guidance
        best_confidence = self.bandit.get_best_confidence(state)
        deviation = context.get("deviation_from_expectation", 0.5)

        if best_confidence >= 0.75 and deviation < 0.5:
            # High confidence + low deviation: implicit guidance
            action = self.bandit.get_best_action(state) or "monitor"
            guidance_mode = GuidanceMode.IMPLICIT
        elif best_confidence < 0.3 and deviation > 0.7:
            # Low confidence + high deviation: keep observing
            action = "monitor"
            guidance_mode = GuidanceMode.OBSERVE_ONLY
        else:
            # Otherwise: explicit deliberation via bandit
            action = self.bandit.choose_action(state, available_actions)
            guidance_mode = GuidanceMode.EXPLICIT

        rationale = self._build_rationale(event, context, action, state, guidance_mode, best_confidence)

        decision = IntelligenceDecision(
            decision_id=f"dec_{uuid.uuid4().hex[:10]}",
            type=decision_type,
            action=action,
            rationale=rationale,
            confidence=self._confidence,
            alternatives=available_actions,
            selected_alternative=action,
            timestamp=datetime.now(timezone.utc),
            state=state,
            guidance_mode=guidance_mode,
        )

        self._decision_history.append({
            "decision": {
                "decision_id": decision.decision_id,
                "action": decision.action,
                "rationale": decision.rationale,
                "confidence": decision.confidence,
                "state": state,
                "guidance_mode": guidance_mode.value,
            },
            "context": {k: v for k, v in context.items() if k != "previous_decisions"},
            "event": event if isinstance(event, dict) else {"raw": str(event)},
        })

        return decision

    def _get_available_actions(self, decision_type: DecisionType, event: Dict[str, Any]) -> List[str]:
        event_type = event.get("event_type", "unknown")
        actions = ["monitor", "log_only"]
        if decision_type == DecisionType.ACT:
            actions.extend(["heal_service", "escalate", "restart_service"])
        elif decision_type == DecisionType.ORIENT:
            actions.extend(["investigate", "analyze_pattern"])
        elif decision_type == DecisionType.OBSERVE:
            actions.extend(["check_service", "run_diagnostics"])
        elif decision_type == DecisionType.LEARN:
            actions.extend(["record_learning", "update_model"])
        if "engine_alert" in event_type:
            actions.extend(["heal_service", "restart_service", "escalate"])
        elif "trend" in event_type:
            actions.extend(["investigate_trend", "generate_content"])
        return list(set(actions))

    def _pattern_matches(self, pattern: Pattern, event: Dict[str, Any]) -> bool:
        for key, value in pattern.conditions.items():
            if event.get(key) != value:
                return False
        return True

    def _classify_event(self, event: Dict[str, Any]) -> DecisionType:
        event_type = event.get("event_type", "unknown")
        if "alert" in event_type or "critical" in event_type:
            return DecisionType.ACT
        elif "trend" in event_type or "pattern" in event_type:
            return DecisionType.ORIENT
        elif "health" in event_type or "status" in event_type:
            return DecisionType.OBSERVE
        elif "learn" in event_type or "feedback" in event_type:
            return DecisionType.LEARN
        else:
            return DecisionType.DECIDE

    def _build_rationale(self, event, context, action, state, guidance_mode, best_confidence):
        parts = [
            f"Event: {event.get('event_type', 'unknown')}",
            f"Action: {action}",
            f"State: {state}",
            f"Guidance: {guidance_mode.value}",
        ]
        if context.get("is_novel"):
            parts.append("NOVEL SIGNAL")
        if context.get("patterns_matched"):
            patterns = [p["name"] for p in context["patterns_matched"]]
            parts.append(f"Patterns: {', '.join(patterns)}")
        parts.append(f"Confidence: {best_confidence:.2f}")
        parts.append(f"Deviation: {context.get('deviation_from_expectation', 0.5):.2f}")
        return " | ".join(parts)

    def learn(self, decision: IntelligenceDecision, outcome: str, success: bool) -> None:
        if success:
            self._confidence = min(1.0, self._confidence + self._learning_rate)
        else:
            self._confidence = max(0.0, self._confidence - self._learning_rate * 0.5)
        for d in self._decision_history:
            if not isinstance(d, dict):
                continue
            dec = d.get("decision")
            did = dec.get("decision_id") if isinstance(dec, dict) else getattr(dec, "decision_id", None)
            if did == decision.decision_id:
                d["outcome"] = outcome
                d["success"] = success
                state = dec.get("state") if isinstance(dec, dict) else getattr(dec, "state", "")
                if state:
                    reward = 1.0 if success else 0.0
                    self.bandit.update(state, decision.action, reward)
                break
        for pattern in self._patterns.values():
            if pattern.recommended_action == decision.action:
                pattern.occurrences += 1
                if success:
                    pattern.success_count += 1
                pattern.confidence = min(1.0, pattern.confidence + (0.1 if success else -0.05))

    def learn_by_decision_id(
        self, decision_id: Optional[str], *, outcome: str, success: bool, score: Optional[float] = None
    ) -> bool:
        if not decision_id:
            return False
        for entry in reversed(self._decision_history):
            if not isinstance(entry, dict):
                continue
            dec = entry.get("decision") or {}
            did = dec.get("decision_id") if isinstance(dec, dict) else getattr(dec, "decision_id", None)
            if did != decision_id:
                continue
            entry["outcome"] = outcome
            entry["success"] = success
            if score is not None:
                entry["outcome_score"] = score
            state = dec.get("state") if isinstance(dec, dict) else getattr(dec, "state", "")
            action = dec.get("action") if isinstance(dec, dict) else getattr(dec, "action", "")
            if state and action:
                reward = score if score is not None else (1.0 if success else 0.0)
                self.bandit.update(state, action, reward)
            self._confidence = min(1.0, self._confidence + self._learning_rate * 0.5) if success else max(0.0, self._confidence - self._learning_rate * 0.25)
            return True
        return False

    def observe_causality(self, cause: str, effect: str) -> None:
        self.bayesian.observe(cause, effect)

    def record_metric(self, metric: str, value: float) -> None:
        self.seasonal_detector.record(metric, value)
        self.adaptive_thresholds.record(metric, value)
        self.predictor.update(metric, value)

    def learn_pattern(self, pattern: Pattern) -> None:
        self._patterns[pattern.id] = pattern

    # Introspection
    def get_causal_insights(self) -> Dict[str, Any]:
        insights = {}
        for cause in self.bayesian._cause_totals.keys():
            effects = self.bayesian.get_effects_for_cause(cause)
            if not effects:
                continue
            chain = self.bayesian.get_causal_chain(cause, max_depth=3)
            insights[cause] = {
                "effects": effects,
                "causal_chain": chain,
                "total_observations": self.bayesian._cause_totals[cause],
            }
        return insights

    def get_action_preferences(self) -> Dict[str, Any]:
        result = {}
        for state, actions in self.bandit._state_actions.items():
            best = max(actions, key=lambda a: actions[a].get("avg_reward", 0.5))
            result[state] = {
                "best_action": best,
                "best_reward": round(actions[best]["avg_reward"], 3),
                "best_attempts": actions[best]["attempts"],
                "all_actions": {
                    a: {
                        "attempts": stats["attempts"],
                        "successes": stats["successes"],
                        "avg_reward": round(stats["avg_reward"], 3),
                    }
                    for a, stats in actions.items()
                },
            }
        return result

    def get_status(self) -> Dict[str, Any]:
        return {
            "confidence": self._confidence,
            "patterns_learned": len(self._patterns),
            "decisions_made": len(self._decision_history),
            "bandit": self.bandit.get_stats(),
            "bayesian": self.bayesian.get_stats(),
            "seasonal": self.seasonal_detector.get_stats(),
            "thresholds": self.adaptive_thresholds.get_stats(),
            "predictor": self.predictor.get_stats(),
            "world_model": self.world_model.get_status(),
            "patterns": {
                name: {
                    "confidence": p.confidence,
                    "occurrences": p.occurrences,
                    "success_rate": p.success_count / max(1, p.occurrences),
                }
                for name, p in self._patterns.items()
            },
        }


# ═══════════════════════════════════════════════════════════════════════════
# PREDICTIVE ENGINE (Retained)
# ═══════════════════════════════════════════════════════════════════════════

class PredictiveEngine:
    def __init__(self):
        self._health_history: Dict[str, deque] = {}
        self._prediction_history: deque = deque(maxlen=100)
        self._anomaly_threshold = 2.0
        self._smoothing_predictor = ExponentialSmoothingPredictor()

    def record_health(self, service: str, health_score: float) -> None:
        if service not in self._health_history:
            self._health_history[service] = deque(maxlen=100)
        self._health_history[service].append({
            "timestamp": datetime.now(timezone.utc),
            "score": health_score,
        })
        self._smoothing_predictor.update(service, health_score)

    async def predict_future_health(self, service: str) -> Optional[Prediction]:
        history = self._health_history.get(service, [])
        if len(history) < 5:
            return None
        predicted_scores = []
        for i in range(1, 6):
            predicted = self._smoothing_predictor.predict(service, i)
            if predicted is not None:
                predicted_scores.append(predicted)
        if not predicted_scores:
            return None
        if any(s < 30 for s in predicted_scores):
            crossing_time = None
            for i, score in enumerate(predicted_scores):
                if score < 30:
                    crossing_time = i + 1
                    break
            if crossing_time:
                time_minutes = crossing_time * 5
                trend = self._smoothing_predictor.get_trend(service)
                return Prediction(
                    service=service,
                    predicted_issue=f"Health degradation detected (trend: {trend:.2f}/check)",
                    probability=min(1.0, (1 - predicted_scores[crossing_time - 1] / 100) * 1.5),
                    estimated_time_minutes=time_minutes,
                    confidence=0.7 if len(history) > 20 else 0.5,
                    recommended_action=f"Proactive healing recommended in {time_minutes:.0f} minutes",
                )
        return None

    async def predict_all(self) -> List[Prediction]:
        predictions = []
        for service in self._health_history.keys():
            prediction = await self.predict_future_health(service)
            if prediction:
                predictions.append(prediction)
        return predictions

    def get_status(self) -> Dict[str, Any]:
        return {
            "services_tracked": len(self._health_history),
            "predictions_made": len(self._prediction_history),
            "history_length": sum(len(h) for h in self._health_history.values()),
            "smoothing_models": self._smoothing_predictor.get_stats(),
        }


# ═══════════════════════════════════════════════════════════════════════════
# MAIN LOOP v4.0 — GENUINE OODA
# ═══════════════════════════════════════════════════════════════════════════

class AutonomousLoop:
    """
    Genuine OODA loop with Boyd's key insights:
      - World model (expectations)
      - Deviation detection (orientation)
      - Adaptive frequency (fast transients)
      - Orientation reshapes observation
      - Implicit vs explicit guidance
    """

    # v4.0 — Adaptive frequency bounds
    BASE_INTERVAL_SECONDS = 15
    MIN_INTERVAL_SECONDS = 3       # Fast under pressure
    MAX_INTERVAL_SECONDS = 60      # Slow when quiet

    MAX_EVENT_QUEUE_SIZE = 1000
    HANDLER_TIMEOUT_SECONDS = 10.0
    LEARNING_BATCH_SIZE = 10

    # v4.0 — Priority signals (learned by orientation)
    ORIENTATION_PRIORITY_DECAY = 0.95  # Priority decays if signal not seen

    def __init__(
        self,
        shared_bus=None,
        meta_engine=None,
        director=None,
        condition_engine=None,
        container=None,
        ai_provider=None,
    ):
        self.shared_bus = shared_bus
        self.meta_engine = meta_engine
        self.director = director
        self.condition_engine = condition_engine
        self.container = container
        # V71.16: legacy loop is an intelligence/proposal producer; consequential
        # business execution is delegated to the governed workforce bridge.
        self._workforce_bridge = None
        self._supabase = None
        self._processed_event_ids: set[str] = set()
        self._event_receipt_lock = asyncio.Lock()

        # Intelligence
        self.intelligence = IntelligenceEngine(ai_provider)
        self.predictive_engine = PredictiveEngine()
        self._memory: deque = deque(maxlen=1000)
        self._learning_batch: List[Dict[str, Any]] = []

        # Async-native state
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

        # v4.0 — Adaptive frequency
        self._current_interval = self.BASE_INTERVAL_SECONDS

        # v4.0 — Orientation reshapes observation
        # Maps signal_key → priority weight (0-1)
        self._observation_priorities: Dict[str, float] = defaultdict(lambda: 0.5)

        # v4.0 — Immediate feedback after action
        self._recent_action_results: deque = deque(maxlen=50)

        # Event queue
        self._event_queue: asyncio.Queue = asyncio.Queue(maxsize=self.MAX_EVENT_QUEUE_SIZE)

        # Circuit breakers
        self._circuit_breakers: dict[str, CircuitBreaker] = {
            "meta_engine": CircuitBreaker("meta_engine"),
            "director": CircuitBreaker("director"),
            "condition_engine": CircuitBreaker("condition_engine"),
            "event_bus": CircuitBreaker("event_bus"),
        }

        # Metrics
        self._metrics = LoopMetrics()

        self._last_health_status: Optional[str] = None
        self._last_health_alert_at: float = 0.0
        self.HEALTH_ALERT_COOLDOWN_SECONDS = 300

        # Event routing
        self._routing: dict[str, Callable[[Dict[str, Any]], Awaitable[None]]] = {
            "trend_detected": self._handle_trend,
            "engine_status_update": self._handle_status_update,
            "service_heartbeat": self._handle_heartbeat,
            "backup_completed": self._handle_backup,
            "incident_report": self._handle_incident,
            "engine_alert": self._handle_alert,
            "anomaly_detected": self._handle_anomaly,
            "publish_request": self._handle_publish_request,
            "publish_command": self._handle_publish_command,
            "task_created": self._handle_task_created,
            "post_published": self._handle_post_published,
            "content_failed": self._handle_content_failed,
            "engagement_data": self._handle_engagement,
            "strategy_recommendation": self._handle_strategy,
            "strategy_executed": self._handle_strategy_executed,
            "brand_health_updated": self._handle_brand_health,
            "task_completed": self._handle_task_completed,
            "task_failed": self._handle_task_failed,
            "intelligent_decision": self._handle_intelligent_decision,
            "prediction_alert": self._handle_prediction_alert,
            "pattern_discovered": self._handle_pattern_discovered,
            "learning_opportunity": self._handle_learning_opportunity,
            "outcome.recorded": self._handle_outcome_recorded,
            "heartbeat.beat": self._handle_heartbeat,
            "post.published": self._handle_post_published,
            "content.failed": self._handle_content_failed,
            "job.completed": self._handle_task_completed,
            "job.failed": self._handle_task_failed,
            "condition.changed": self._handle_brand_health,
            "action.proposed": self._handle_publish_request,
            "council.decided": self._handle_intelligent_decision,
            "reflex.fired": self._handle_alert,
            "dream.insight": self._handle_learning_opportunity,
            "workforce.execution.result": self._handle_workforce_execution_result,
            "circulation.blockage": self._handle_incident,
            "pulse.stagnant": self._handle_anomaly,
            "director.proposal": self._handle_strategy,
            "strategy_execute": self._handle_strategy_execute,
        }

        logger.info("🧠 Autonomous Loop v4.0 GENUINE OODA initialized (World Model + Adaptive Frequency + Implicit Guidance)")

    # ═══════════════════════════════════════════════════════════════════
    # LIFECYCLE
    # ═══════════════════════════════════════════════════════════════════

    def set_workforce_bridge(self, bridge: Any) -> None:
        """Install the canonical V71 workforce bridge; no provider authority is added."""
        self._workforce_bridge = bridge

    @property
    def service_name(self) -> str:
        return "autonomous_loop"

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        await self._subscribe_to_events()
        self._task = asyncio.create_task(self._loop(), name="autonomous-loop")
        logger.info("🧠 Autonomous Loop v4.0 GENUINE OODA started")

    async def stop(self) -> None:
        if not self._running:
            return
        logger.info("Stopping Autonomous Loop...")
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("Autonomous Loop stopped")

    async def check_health_async(self) -> HealthReport:
        try:
            if not self._running:
                return HealthReport(status=HealthStatus.UNKNOWN, service_name=self.service_name, error_message="Loop is not running")
            return HealthReport(
                status=HealthStatus.HEALTHY,
                service_name=self.service_name,
                metadata={
                    "events_processed": self._metrics.events_processed,
                    "events_failed": self._metrics.events_failed,
                    "event_queue_size": self._event_queue.qsize(),
                    "decisions_made": self._metrics.decisions_made,
                    "patterns_learned": self._metrics.patterns_learned,
                    "predictions_made": self._metrics.predictions_made,
                    "verified_outcomes_learned": self._metrics.verified_outcomes_learned,
                    "world_model_updates": self._metrics.world_model_updates,
                    "deviations_detected": self._metrics.deviations_detected,
                    "implicit_decisions": self._metrics.implicit_decisions,
                    "explicit_decisions": self._metrics.explicit_decisions,
                    "current_interval_seconds": self._current_interval,
                    "current_volatility": self.intelligence.world_model.compute_volatility(),
                    "intelligence": self.intelligence.get_status(),
                    "circuit_breakers": {name: breaker.get_stats() for name, breaker in self._circuit_breakers.items()},
                },
            )
        except Exception as exc:
            return HealthReport(status=HealthStatus.UNHEALTHY, service_name=self.service_name, error_message=str(exc))

    # ═══════════════════════════════════════════════════════════════════
    # EVENT SUBSCRIPTION
    # ═══════════════════════════════════════════════════════════════════

    async def _subscribe_to_events(self) -> None:
        bus = self.shared_bus
        if bus is None and self.container:
            try:
                from core.unified_bus import UnifiedEventBus
                bus = self.container.try_resolve(UnifiedEventBus)
            except Exception:
                bus = None
        if not bus:
            logger.warning("No event bus — AutonomousLoop subscription skipped")
            return
        for event_type in self._routing.keys():
            try:
                await bus.subscribe(event_type, self._enqueue_event)
                logger.debug(f"Subscribed to event: {event_type}")
            except Exception as exc:
                logger.warning(f"Failed to subscribe to {event_type}: {exc}")

    async def _enqueue_event(self, event_data: Any = None, payload: Dict[str, Any] = None, **kwargs) -> None:
        try:
            if payload is None and isinstance(event_data, dict):
                if "event_name" in event_data or "payload" in event_data:
                    event_name = event_data.get("event_name") or event_data.get("event_type") or "unknown"
                    payload_data = event_data.get("payload", event_data)
                else:
                    event_name = event_data.get("event_type") or kwargs.get("event_name") or "unknown"
                    payload_data = event_data
            elif isinstance(event_data, dict) and payload is not None:
                event_name = event_data.get("event_name", str(event_data))
                payload_data = payload if isinstance(payload, dict) else {"data": payload}
            else:
                event_name = str(event_data) if event_data is not None else "unknown"
                payload_data = payload if isinstance(payload, dict) else (payload or {})
                if not isinstance(payload_data, dict):
                    payload_data = {"data": payload_data}

            self._event_queue.put_nowait({
                "event_type": event_name,
                "payload": payload_data,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "correlation_id": (
                    payload_data.get("correlation_id", uuid.uuid4().hex)
                    if isinstance(payload_data, dict) else uuid.uuid4().hex
                ),
            })
        except asyncio.QueueFull:
            logger.warning("Event queue full — dropping event")
            self._metrics.events_failed += 1
        except Exception as exc:
            logger.warning("Enqueue event failed: %s", exc)
            self._metrics.events_failed += 1

    # ═══════════════════════════════════════════════════════════════════
    # MAIN LOOP — v4.0 with adaptive frequency
    # ═══════════════════════════════════════════════════════════════════

    async def _loop(self) -> None:
        """OODA loop — Observe, Orient, Decide, Act — with adaptive frequency."""
        while self._running:
            try:
                await self._ooda_cycle()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception(f"OODA cycle error: {exc}")

            # v4.0 — Boyd's "fast transient": speed up when volatile
            await asyncio.sleep(self._current_interval)

    async def _ooda_cycle(self) -> None:
        """
        One complete OODA cycle.

        1. OBSERVE — drain event queue (weighted by orientation)
        2. ORIENT — compare against world model, detect deviation
        3. DECIDE — implicit guidance or explicit deliberation
        4. ACT — execute + capture immediate feedback
        Then: update model, reshape observation, adapt frequency.
        """
        # 1. OBSERVE
        events_processed = await self._observe_phase()

        # 2. ORIENT + 3. DECIDE + 4. ACT are inside _process_event
        # but we track metrics separately

        # Post-cycle: predictions
        await self._run_predictions()

        # Post-cycle: batch learning
        if len(self._learning_batch) >= self.LEARNING_BATCH_SIZE:
            await self._process_learning_batch()

        # Post-cycle: intelligence metrics
        await self._update_intelligence()

        # v4.0 — Adaptive frequency based on volatility
        await self._adapt_frequency(events_processed)

        # v4.0 — Orientation reshapes observation
        await self._reshape_observation_priorities()

    # ═══════════════════════════════════════════════════════════════════
    # PHASE 1: OBSERVE (v4.0 — weighted by orientation)
    # ═══════════════════════════════════════════════════════════════════

    async def _observe_phase(self) -> int:
        """Observe: drain event queue, count processed."""
        events_processed = 0
        while not self._event_queue.empty():
            try:
                event_data = self._event_queue.get_nowait()
                await self._process_event(event_data)
                events_processed += 1
            except asyncio.QueueEmpty:
                break
            except Exception as exc:
                logger.error(f"Event processing error: {exc}")
                self._metrics.events_failed += 1

        if events_processed == 0:
            await self.intelligence.world_model.observe_idle()
        else:
            self._metrics.world_model_updates += events_processed

        return events_processed

    # ═══════════════════════════════════════════════════════════════════
    # v4.0 — ADAPTIVE FREQUENCY
    # ═══════════════════════════════════════════════════════════════════

    async def _adapt_frequency(self, events_processed: int) -> None:
        """
        Boyd: cycle faster than the environment changes.

        Speed up under:
          - High event volume
          - High volatility
          - Recent critical decisions

        Slow down under:
          - Idle
          - Low volatility
        """
        volatility = self.intelligence.world_model.compute_volatility()
        queue_pressure = self._event_queue.qsize() / max(self.MAX_EVENT_QUEUE_SIZE, 1)

        # Priority signals (high volatility signals present)
        priority_pressure = sum(
            w for w in self._observation_priorities.values() if w > 0.7
        ) / max(len(self._observation_priorities), 1)

        # Composite pressure score
        pressure = (
            min(1.0, events_processed / 10.0) * 0.4 +
            volatility * 0.3 +
            queue_pressure * 0.2 +
            priority_pressure * 0.1
        )

        if pressure > 0.7:
            new_interval = self.MIN_INTERVAL_SECONDS
            if self._current_interval != new_interval:
                self._metrics.adaptive_speedups += 1
                logger.debug(f"⚡ OODA speeding up: {self._current_interval}s → {new_interval}s (pressure={pressure:.2f})")
        elif pressure > 0.4:
            new_interval = (self.MIN_INTERVAL_SECONDS + self.BASE_INTERVAL_SECONDS) / 2
        elif pressure > 0.15:
            new_interval = self.BASE_INTERVAL_SECONDS
        else:
            # Very quiet — slow down
            new_interval = min(
                self.MAX_INTERVAL_SECONDS,
                self._current_interval * 1.2,
            )

        self._current_interval = new_interval

    # ═══════════════════════════════════════════════════════════════════
    # v4.0 — ORIENTATION RESHAPES OBSERVATION
    # ═══════════════════════════════════════════════════════════════════

    async def _reshape_observation_priorities(self) -> None:
        """
        Boyd's feedback loop: Orientation feeds back into Observation.

        When we learn something matters, we look for it harder.
        When something stops mattering, we decay its priority.
        """
        # Decay all priorities
        for key in list(self._observation_priorities.keys()):
            self._observation_priorities[key] *= self.ORIENTATION_PRIORITY_DECAY

        # Boost priorities for signals we've recently seen and acted on
        for result in list(self._recent_action_results):
            signal_key = result.get("signal_key")
            if signal_key:
                # Boost if successful action
                if result.get("success"):
                    self._observation_priorities[signal_key] = min(
                        1.0,
                        self._observation_priorities[signal_key] + 0.15,
                    )
                else:
                    # Failed actions: still boost (need to watch more)
                    self._observation_priorities[signal_key] = min(
                        1.0,
                        self._observation_priorities[signal_key] + 0.10,
                    )

        # Boost for high-deviation signals
        top_expectations = self.intelligence.world_model.get_top_expectations(limit=5)
        for exp in top_expectations:
            signal = exp.get("signal")
            if signal:
                self._observation_priorities[signal] = max(
                    self._observation_priorities[signal],
                    min(1.0, exp.get("frequency", 0) / 10.0),
                )

        self._metrics.orientation_reshapes += 1

    # ═══════════════════════════════════════════════════════════════════
    # EVENT PROCESSING (OODA core)
    # ═══════════════════════════════════════════════════════════════════

    def _get_supabase(self):
        if self._supabase is not None:
            return self._supabase
        try:
            from supabase import Client as SupabaseClient
            if self.container and self.container.is_registered(SupabaseClient):
                self._supabase = self.container.resolve(SupabaseClient)
        except Exception:
            self._supabase = None
        return self._supabase

    async def _claim_event(self, event_data: Dict[str, Any]) -> bool:
        event_id = str(event_data.get("event_id") or event_data.get("id") or "").strip()
        if not event_id:
            return True
        async with self._event_receipt_lock:
            if event_id in self._processed_event_ids:
                return False
            db = self._get_supabase()
            if db is not None:
                try:
                    db.table("autonomous_event_receipts").insert({
                        "event_id": event_id,
                        "workspace_id": event_data.get("workspace_id"),
                        "event_type": str(event_data.get("event_type", "unknown")),
                        "status": "processing",
                    }).execute()
                except Exception as exc:
                    msg = str(exc).lower()
                    if "duplicate" in msg or "unique" in msg or "23505" in msg:
                        self._processed_event_ids.add(event_id)
                        return False
                    logger.warning("Durable event receipt unavailable: %s", exc)
            self._processed_event_ids.add(event_id)
            return True

    async def _mark_event_processed(self, event_data: Dict[str, Any], success: bool, error: Optional[str] = None) -> None:
        event_id = str(event_data.get("event_id") or event_data.get("id") or "").strip()
        if not event_id:
            return
        db = self._get_supabase()
        if db is None:
            return
        try:
            db.table("autonomous_event_receipts").update({
                "status": "processed" if success else "failed",
                "error": (error or "")[:500] if error else None,
                "processed_at": datetime.now(timezone.utc).isoformat(),
            }).eq("event_id", event_id).eq("status", "processing").execute()
        except Exception as exc:
            logger.warning("Failed to finalize event receipt %s: %s", event_id, exc)

    async def _process_event(self, event_data: Dict[str, Any]) -> None:
        """
        Full OODA cycle for a single event.

        v4.0: The four steps are now explicit.
        """
        # ── IDEMPOTENCY ─────────────────────────────────────────────
        if not await self._claim_event(event_data):
            return
        event_type = event_data.get("event_type", "unknown")
        payload = event_data.get("payload", {})
        correlation_id = event_data.get("correlation_id", uuid.uuid4().hex)

        memory = Memory(
            event_id=f"mem_{uuid.uuid4().hex[:10]}",
            timestamp=datetime.now(timezone.utc),
            event_type=event_type,
            payload=payload,
            correlation_id=correlation_id,
        )
        self._memory.append(memory)

        handler = self._routing.get(str(event_type))
        if not handler:
            await self._mark_event_processed(event_data, True)
            self._metrics.events_processed += 1
            return

        # ── 1. OBSERVE ──────────────────────────────────────────────
        # Priority check: skip low-priority signals when busy
        priority = self._observation_priorities.get(event_type, 0.5)
        if self._event_queue.qsize() > 20 and priority < 0.3:
            logger.debug(f"⏭️ Skipping low-priority event: {event_type}")
            await self._mark_event_processed(event_data, True)
            self._metrics.events_processed += 1
            return

        # ── 2. ORIENT ───────────────────────────────────────────────
        event_context = {
            "event_type": str(event_type),
            "payload": payload,
            "engine": payload.get("engine", "unknown") if isinstance(payload, dict) else "unknown",
            "status": payload.get("status", "unknown") if isinstance(payload, dict) else "unknown",
            "timestamp": event_data.get("timestamp"),
            "correlation_id": correlation_id,
        }
        context = await self.intelligence.analyze_context(event_context)

        if context.get("is_novel"):
            self._metrics.deviations_detected += 1

        # ── 3. DECIDE ───────────────────────────────────────────────
        try:
            async with asyncio.timeout(self.HANDLER_TIMEOUT_SECONDS):
                # Execute the handler (which decides internally)
                await handler(payload)
                decision_success = True
        except asyncio.TimeoutError:
            logger.error("Handler timeout for %s", event_type)
            self._metrics.events_failed += 1
            decision_success = False
        except Exception as exc:
            logger.error(f"Handler error for {event_type}: {exc}")
            self._metrics.events_failed += 1
            decision_success = False

        # ── 4. ACT (already done inside handler) + Capture feedback ─
        if decision_success:
            # Update metrics
            await self._mark_event_processed(event_data, True)
            self._metrics.events_processed += 1
            self._metrics.events_by_type[str(event_type)] = (
                self._metrics.events_by_type.get(str(event_type), 0) + 1
            )

            # v4.0: Record action result for orientation feedback
            signal_key = self.intelligence.signal_key_for_event(event_context)
            self._recent_action_results.append({
                "signal_key": signal_key,
                "event_type": str(event_type),
                "success": True,
                "timestamp": time.time(),
            })

            # Track implicit vs explicit
            # (Handlers use the intelligence engine which sets guidance mode)
            # We approximate here — handlers that publish actions are implicit
            if priority > 0.6:
                self._metrics.implicit_decisions += 1
            else:
                self._metrics.explicit_decisions += 1

    # ═══════════════════════════════════════════════════════════════════
    # EVENT PUBLISHING (Retained)
    # ═══════════════════════════════════════════════════════════════════

    _PLATFORM_EVENT_TYPES = frozenset({
        "engine_alert", "engine_status_update", "service_heartbeat",
        "heartbeat.beat", "heartbeat.system", "heal_command",
        "escalation_request", "anomaly_response", "prediction_alert",
        "pattern_discovered", "incident_report",
    })

    async def _publish_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        breaker = self._circuit_breakers["event_bus"]
        payload = dict(payload or {})
        workspace_id = payload.get("workspace_id")
        if not workspace_id and event_type in self._PLATFORM_EVENT_TYPES:
            workspace_id = "platform"
            payload.setdefault("workspace_id", "platform")

        async def _publish():
            if self.shared_bus:
                try:
                    publish_kw = {"source": "autonomous_loop"}
                    if workspace_id:
                        publish_kw["workspace_id"] = workspace_id
                    if hasattr(self.shared_bus, "publish_event"):
                        await self.shared_bus.publish_event(event_type, payload, **publish_kw)
                    elif hasattr(self.shared_bus, "publish"):
                        await self.shared_bus.publish(event_type, payload, **publish_kw)
                    else:
                        raise AttributeError("event bus has no publish method")
                except TypeError:
                    if hasattr(self.shared_bus, "publish"):
                        await self.shared_bus.publish(event_type, payload)
                    else:
                        raise
                except Exception as exc:
                    if "has no attribute" in str(exc):
                        if hasattr(self.shared_bus, "publish"):
                            await self.shared_bus.publish(event_type, payload)
                        else:
                            raise                    else:
                        raise

        try:
            await breaker.execute(_publish)
        except CircuitBreakerOpenError:
            logger.warning("Event bus circuit breaker open")
            await self._store_for_retry(event_type, payload)
        except Exception as exc:
            logger.error(f"Failed to publish event {event_type}: {exc}")
            self._metrics.events_failed += 1

    async def _store_for_retry(self, event_type: str, payload: Dict[str, Any]) -> None:
        try:
            if not hasattr(self, "_retry_queue"):
                self._retry_queue = deque(maxlen=100)
            self._retry_queue.append({
                "event_type": event_type,
                "payload": payload,
                "timestamp": datetime.now(timezone.utc),
                "attempts": 0,
            })
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════════════
    # EVENT HANDLERS (Preserved from v3.4)
    # ═══════════════════════════════════════════════════════════════════

    async def _handle_trend(self, payload: Dict[str, Any]) -> None:
        topic = payload.get("topic", "")
        source = payload.get("source", "")
        logger.info(f"🔍 SENSORY: Trend detected — '{topic}' from {source}")
        self.intelligence.record_metric("trend_detected", 1.0)
        event_context = {"event_type": "trend_detected", "payload": payload}
        context = await self.intelligence.analyze_context(event_context)
        decision = await self.intelligence.generate_decision(event_context, context)
        if decision.action != "ignore_trend":
            await self._publish_event("trend_analysis_request", {
                "topic": topic, "source": source,
                "action": decision.action, "rationale": decision.rationale,
                "correlation_id": uuid.uuid4().hex,
            })

    async def _handle_status_update(self, payload: Dict[str, Any]) -> None:
        engine = payload.get("engine", "")
        status = payload.get("status", "")
        if status == "healthy":
            self.predictive_engine.record_health(engine, 90)
        elif status == "degraded":
            self.predictive_engine.record_health(engine, 50)
        else:
            self.predictive_engine.record_health(engine, 10)

    async def _handle_heartbeat(self, payload: Dict[str, Any]) -> None:
        logger.debug(f"💓 HEARTBEAT: {payload.get('service', '')}")

    async def _handle_backup(self, payload: Dict[str, Any]) -> None:
        logger.info(f"💾 BACKUP: {payload.get('status', '')}")

    async def _handle_incident(self, payload: Dict[str, Any]) -> None:
        incident = payload.get("incident", "")
        engine = payload.get("engine", "unknown")
        logger.warning(f"🚨 INCIDENT: {incident}")
        self.intelligence.observe_causality(engine, "incident")
        event_context = {"event_type": "incident_report", "payload": payload}
        context = await self.intelligence.analyze_context(event_context)
        decision = await self.intelligence.generate_decision(event_context, context)
        await self._publish_event("engine_alert", {
            "engine": engine, "status": "critical", "message": incident,
            "action": decision.action, "correlation_id": uuid.uuid4().hex,
        })

    async def _handle_alert(self, payload: Dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            payload = {
                "engine": getattr(payload, "engine", "unknown"),
                "status": getattr(payload, "status", "unknown"),
                "message": str(payload),
            }
        engine = payload.get("engine", "")
        status = payload.get("status", "")
        message = payload.get("message", "")

        if engine == "system":
            logger.warning("👔 META/LOOP: Alert — system is %s%s", status, f" — {message}" if message else "")
        else:
            logger.warning("👔 DIRECTOR: Alert — %s is %s%s", engine, status, f" — {message}" if message else "")

        self.intelligence.record_metric(f"alert:{engine}", 1.0)
        self.intelligence.observe_causality(engine, status)

        try:
            from services.reliability import get_reliability_engine, init_reliability_engine
            rel = get_reliability_engine()
            if rel is None:
                try:
                    rel = await init_reliability_engine(container=self.container)
                except Exception:
                    rel = None
            if rel is not None:
                await rel.handle_engine_alert(payload if isinstance(payload, dict) else {
                    "engine": engine, "status": status, "message": message,
                })
        except Exception as rel_exc:
            logger.debug("Reliability engine handle failed: %s", rel_exc)

        event_context = {"event_type": "engine_alert", "payload": payload, "engine": engine, "status": status}
        try:
            async with asyncio.timeout(12.0):
                context = await self.intelligence.analyze_context(event_context)
                decision = await self.intelligence.generate_decision(event_context, context)
        except asyncio.TimeoutError:
            logger.warning("engine_alert intelligence timed out")
            return
        except Exception as intel_exc:
            logger.debug("engine_alert intelligence skipped: %s", intel_exc)
            return

        action = decision.action if hasattr(decision, "action") else (
            decision.get("action") if isinstance(decision, dict) else "monitor"
        )

        if action == "heal_service":
            await self._publish_event("heal_command", {
                "engine": engine, "reason": status, "correlation_id": uuid.uuid4().hex,
            })
        elif action == "escalate":
            await self._publish_event("escalation_request", {
                "engine": engine, "reason": status, "correlation_id": uuid.uuid4().hex,
            })

    async def _handle_anomaly(self, payload: Dict[str, Any]) -> None:
        logger.warning(f"👔 DIRECTOR: Anomaly — {payload}")

    async def _handle_publish_request(self, payload: Dict[str, Any]) -> None:
        """Turn autonomous publishing intent into a governed workforce proposal."""
        workspace_id = str(payload.get("workspace_id") or "").strip()
        if not workspace_id:
            logger.warning("Autonomous publish proposal blocked: missing workspace_id")
            return
        if self._workforce_bridge is None:
            logger.warning("Autonomous publish proposal withheld: workforce bridge unavailable")
            return
        proposal = await self._workforce_bridge.propose(
            payload, capability="publish_content", workflow=payload.get("workflow")
        )
        await self._publish_event("workforce.proposal", {
            **payload,
            **proposal.to_dict(),
            "action": "publish_content",
            "workspace_id": workspace_id,
        })

    async def _handle_publish_command(self, payload: Dict[str, Any]) -> None:
        """Legacy publish command is now a routing signal, never a direct executor.

        V71.17 requires an existing durable workforce run. A bare legacy
        publish_command is fenced rather than falling back to the old publish
        pipeline. This removes the last obvious AutonomousLoop side-effect path.
        """
        workspace_id = str(payload.get("workspace_id") or "").strip()
        run_id = str(payload.get("run_id") or payload.get("workforce_run_id") or "").strip()
        if not workspace_id or not run_id:
            logger.warning("Legacy publish_command fenced: durable workforce run required")
            await self._publish_event("action.blocked", {
                **payload, "workspace_id": workspace_id, "run_id": run_id,
                "decision": "FENCED", "reason": "durable_workforce_run_required",
                "execution_requested": False, "authority_granted": False,
            })
            return
        factory = getattr(getattr(self.container, "app", None), "state", None)
        coordinator_factory = None
        try:
            # The application stores the V71.17 factory on app.state; container
            # implementations may expose the app, but the loop also accepts an
            # injected factory for tests/alternate runtimes.
            coordinator_factory = getattr(self, "_workforce_execution_coordinator_factory", None)
            if coordinator_factory is None and factory is not None:
                coordinator_factory = getattr(factory, "workforce_execution_coordinator_factory", None)
        except Exception:
            coordinator_factory = None
        if coordinator_factory is None:
            logger.warning("Legacy publish_command fenced: V71.17 coordinator unavailable")
            return
        try:
            coordinator = coordinator_factory(workspace_id)
            result = await coordinator.execute_run(run_id)
            await self._publish_event("workforce.execution.result", {
                **payload, **result.to_dict(), "workspace_id": workspace_id, "run_id": run_id,
            })
        except Exception as exc:
            logger.warning("V71.17 workforce execution routing blocked: %s", exc)

    async def _handle_task_created(self, payload: Dict[str, Any]) -> None:
        logger.debug(f"📋 TASK: {payload.get('description', '')}")

    async def _handle_post_published(self, payload: Dict[str, Any]) -> None:
        topic = payload.get("topic", "")
        brand = payload.get("brand", "")
        logger.info(f"🧠 STRATEGY: Analyzing post '{topic}' for {brand}")
        self.intelligence.observe_causality("publish", "post_published")

    async def _handle_content_failed(self, payload: Dict[str, Any]) -> None:
        error = payload.get("error", "")
        brand = payload.get("brand", "")
        logger.warning(f"⚠️ CONTENT FAILED: {brand} — {error}")
        self.intelligence.observe_causality("publish", "content_failed")

    async def _handle_engagement(self, payload: Dict[str, Any]) -> None:
        logger.info(f"📊 ENGAGEMENT: {payload}")

    async def _handle_strategy(self, payload: Dict[str, Any]) -> None:
        action = payload.get("action", "")
        logger.info(f"🎯 STRATEGY: {action}")

    async def _handle_strategy_execute(self, payload: Dict[str, Any]) -> None:
        """Convert legacy strategy execution into a V71 workforce proposal.

        The legacy loop no longer calls the action pipeline itself. A configured
        workforce identity/plan may be durably admitted for governance; missing
        configuration is advisory-only.
        """
        action = str(payload.get("action") or "").strip()
        workspace_id = str(payload.get("workspace_id") or "").strip()
        if not action or not workspace_id:
            return
        if self._workforce_bridge is None:
            logger.warning("V71.16 workforce bridge unavailable; strategy withheld workspace=%s action=%s", workspace_id, action)
            await self._publish_event("action.blocked", {**payload, "action": action, "workspace_id": workspace_id,
                                                        "decision": "SUGGEST",
                                                        "reason": "workforce_bridge_unavailable",
                                                        "execution_requested": False, "authority_granted": False})
            return
        proposal = await self._workforce_bridge.propose(payload, capability=action, workflow=payload.get("workflow"))
        await self._publish_event("workforce.proposal", {
            **payload,
            **proposal.to_dict(),
            "action": action,
            "workspace_id": workspace_id,
        })

    async def _handle_strategy_executed(self, payload: Dict[str, Any]) -> None:
        logger.info("📋 STRATEGY DISPATCHED: %s", payload)

    async def _handle_brand_health(self, payload: Dict[str, Any]) -> None:
        brand_id = payload.get("brand_id", "")
        health_score = payload.get("health_score", 0)
        self.predictive_engine.record_health(f"brand:{brand_id}", health_score)

    async def _handle_task_completed(self, payload: Dict[str, Any]) -> None:
        description = payload.get("description", "")
        logger.info("📋 LEGACY OBSERVATION RECEIVED: %s", description)

    async def _handle_task_failed(self, payload: Dict[str, Any]) -> None:
        description = payload.get("description", "")
        error = payload.get("error", "")
        logger.warning(f"❌ LEARNED: '{description}' failed — {error}")
        await self._record_learning(payload, success=False)

    async def _handle_outcome_recorded(self, payload: Dict[str, Any]) -> None:
        outcome = str(payload.get("outcome", "")).lower()
        if outcome not in {"success", "failure", "partial"}:
            return

        score = payload.get("score")
        if score is not None:
            try:
                score = max(0.0, min(1.0, float(score)))
            except (TypeError, ValueError):
                score = None

        success = outcome == "success" if score is None else score >= 0.6
        decision_id = payload.get("decision_id")
        updated = self.intelligence.learn_by_decision_id(
            decision_id, outcome=outcome, success=success, score=score
        )
        if updated:
            self._metrics.verified_outcomes_learned += 1
            logger.info("🧠 LEARNED verified outcome decision=%s outcome=%s", decision_id, outcome)

    async def _handle_workforce_execution_result(self, payload: Dict[str, Any]) -> None:
        """Accept only explicitly verified workforce outcomes into learning.

        A transport/execution result is not itself business-outcome evidence.
        The verifier must mark the result as verified and provide an outcome
        before the learning system is allowed to update decision intelligence.
        """
        if payload.get("verified_outcome") is not True:
            logger.debug("Workforce result withheld from learning: outcome not verified")
            return
        evidence = payload.get("outcome_evidence")
        if not isinstance(evidence, dict):
            logger.warning("Verified workforce result missing outcome_evidence")
            return
        outcome = str(evidence.get("outcome") or "").lower()
        decision_id = evidence.get("decision_id") or payload.get("decision_id")
        if outcome not in {"success", "failure", "partial"} or not decision_id:
            logger.warning("Verified workforce result missing valid outcome/decision identity")
            return
        await self._handle_outcome_recorded({
            "outcome": outcome,
            "score": evidence.get("score"),
            "decision_id": decision_id,
            "correlation_id": payload.get("correlation_id"),
            "source": "verified_workforce_outcome",
        })

    async def _handle_intelligent_decision(self, payload: Dict[str, Any]) -> None:
        """Consume only schema-valid decision events.

        The legacy loop is advisory/fenced from consequential execution, but it
        must not turn a malformed event into a believable decision by inventing
        ``action=unknown``. Missing/invalid action is quarantined as malformed
        input and is never counted as a decision.
        """
        action = payload.get("action") if isinstance(payload, dict) else None
        if not isinstance(action, str) or not action.strip():
            logger.error(
                "Rejected malformed intelligent_decision: missing/invalid action decision_id=%s",
                payload.get("decision_id") if isinstance(payload, dict) else None,
            )
            return
        self._metrics.decisions_made += 1
        logger.info("🧠 INTELLIGENT DECISION: %s", action.strip())

    async def _handle_prediction_alert(self, payload: Dict[str, Any]) -> None:
        service = payload.get("service", "unknown")
        logger.warning(f"🔮 PREDICTION: {service}")
        self._metrics.predictions_made += 1

    async def _handle_pattern_discovered(self, payload: Dict[str, Any]) -> None:
        pattern_name = payload.get("name", "unknown")
        logger.info(f"📊 PATTERN DISCOVERED: {pattern_name}")
        self._metrics.patterns_learned += 1

    async def _handle_learning_opportunity(self, payload: Dict[str, Any]) -> None:
        logger.info(f"📚 LEARNING: {payload.get('insight', '')}")
        self._learning_batch.append(payload)

    async def _record_learning(self, payload: Dict[str, Any], success: bool) -> None:
        self._learning_batch.append({
            "type": "task_outcome",
            "success": success,
            "payload": payload,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    async def _process_learning_batch(self) -> None:
        batch = self._learning_batch[:]
        self._learning_batch = []

        for item in batch:
            if item.get("type") == "task_outcome":
                success = item.get("success", False)
                if success:
                    self.intelligence._confidence = min(1.0, self.intelligence._confidence + 0.01)
                else:
                    self.intelligence._confidence = max(0.0, self.intelligence._confidence - 0.005)

        patterns = await self._discover_patterns(batch)
        for pattern in patterns:
            self.intelligence.learn_pattern(pattern)
            await self._publish_event("pattern_discovered", {
                "name": pattern.name,
                "description": pattern.description,
                "confidence": pattern.confidence,
                "correlation_id": uuid.uuid4().hex,
            })

    async def _discover_patterns(self, batch: List[Dict[str, Any]]) -> List[Pattern]:
        patterns = []
        event_types = {}
        for item in batch:
            event_type = item.get("type", "unknown")
            event_types[event_type] = event_types.get(event_type, 0) + 1
        for event_type, count in event_types.items():
            if count >= 3:
                patterns.append(Pattern(
                    id=f"pat_{uuid.uuid4().hex[:8]}",
                    name=f"Recurring {event_type}",
                    description=f"{event_type} occurred {count} times",
                    conditions={"type": event_type},
                    recommended_action=f"optimize_{event_type}",
                    confidence=min(0.9, 0.5 + count * 0.1),
                    occurrences=count,
                ))
        return patterns

    # ═══════════════════════════════════════════════════════════════════
    # PREDICTIONS (Retained)
    # ═══════════════════════════════════════════════════════════════════

    async def _run_predictions(self) -> None:
        predictions = await self.predictive_engine.predict_all()
        for prediction in predictions:
            if prediction.probability > 0.7:
                await self._publish_event("prediction_alert", {
                    "service": prediction.service,
                    "predicted_issue": prediction.predicted_issue,
                    "probability": prediction.probability,
                    "estimated_time_minutes": prediction.estimated_time_minutes,
                    "recommended_action": prediction.recommended_action,
                    "correlation_id": uuid.uuid4().hex,
                })
                await self._proactive_heal(prediction)

    async def _proactive_heal(self, prediction: Prediction) -> None:
        logger.info(f"🛡️ PROACTIVE HEALING: {prediction.service}")
        self._metrics.proactive_healing_attempts += 1
        await self._publish_event("heal_command", {
            "engine": prediction.service,
            "reason": f"Predicted failure in {prediction.estimated_time_minutes:.0f} minutes",
            "action": prediction.recommended_action,
            "correlation_id": uuid.uuid4().hex,
            "requires_verification": True,
        })

    # ═══════════════════════════════════════════════════════════════════
    # SYSTEM HEALTH (Retained)
    # ═══════════════════════════════════════════════════════════════════

    async def _check_system_health(self) -> None:
        if not self.meta_engine:
            return
        try:
            if not hasattr(self.meta_engine, "check_health_async"):
                return
            health = await self.meta_engine.check_health_async()
            status = str(health.get("status", "unknown")).lower()
            now = time.monotonic()
            prev = self._last_health_status
            self._last_health_status = status
            is_bad = status in ("degraded", "unhealthy")
            transition = prev is not None and prev != status
            cooled = (now - self._last_health_alert_at) >= self.HEALTH_ALERT_COOLDOWN_SECONDS
            if is_bad:
                self.predictive_engine.record_health("meta_engine", 30 if status == "unhealthy" else 50)
                if prev is None or transition or cooled:
                    detail = {
                        "engine": "system", "status": status,
                        "message": health.get("message") or f"Meta {status}",
                        "correlation_id": uuid.uuid4().hex, "workspace_id": "platform",
                    }
                    await self._publish_event("engine_alert", detail)
                    self._last_health_alert_at = now
            elif prev is not None and prev in ("degraded", "unhealthy"):
                logger.info("META: system recovered — status=%s", status)
                self.predictive_engine.record_health("meta_engine", 90)
                self._last_health_alert_at = now
        except Exception as exc:
            logger.debug(f"Health check failed: {exc}")

    async def _update_intelligence(self) -> None:
        self._metrics.intelligence_confidence = self.intelligence._confidence
        self._metrics.learning_rate = self.intelligence._learning_rate

    # ═══════════════════════════════════════════════════════════════════
    # STATUS & INTROSPECTION
    # ═══════════════════════════════════════════════════════════════════

    def get_status(self) -> Dict[str, Any]:
        return {
            "running": self._running,
            "events_processed": self._metrics.events_processed,
            "events_failed": self._metrics.events_failed,
            "event_queue_size": self._event_queue.qsize(),
            "decisions_made": self._metrics.decisions_made,
            "decisions_successful": self._metrics.decisions_successful,
            "patterns_learned": self._metrics.patterns_learned,
            "predictions_made": self._metrics.predictions_made,
            "proactive_healings": self._metrics.proactive_healing_attempts,
            "verified_outcomes_learned": self._metrics.verified_outcomes_learned,
            # v4.0 OODA metrics
            "ooda": {
                "current_interval_seconds": self._current_interval,
                "base_interval_seconds": self.BASE_INTERVAL_SECONDS,
                "min_interval_seconds": self.MIN_INTERVAL_SECONDS,
                "max_interval_seconds": self.MAX_INTERVAL_SECONDS,
                "world_model_updates": self._metrics.world_model_updates,
                "deviations_detected": self._metrics.deviations_detected,
                "implicit_decisions": self._metrics.implicit_decisions,
                "explicit_decisions": self._metrics.explicit_decisions,
                "adaptive_speedups": self._metrics.adaptive_speedups,
                "orientation_reshapes": self._metrics.orientation_reshapes,
                "current_volatility": self.intelligence.world_model.compute_volatility(),
                "priority_signals": dict(sorted(
                    self._observation_priorities.items(),
                    key=lambda x: x[1],
                    reverse=True,
                )[:10]),
            },
            "intelligence": self.intelligence.get_status(),
            "prediction": self.predictive_engine.get_status(),
            "circuit_breakers": {name: breaker.get_stats() for name, breaker in self._circuit_breakers.items()},
            "services_connected": {
                "meta_engine": "connected" if self.meta_engine else "not connected",
                "director": "connected" if self.director else "not connected",
                "condition_engine": "connected" if self.condition_engine else "not connected",
                "intelligence": "active",
                "predictive_engine": "active",
                "world_model": "active",
            },
        }

    async def get_intelligence_report(self) -> Dict[str, Any]:
        return {
            "intelligence": self.intelligence.get_status(),
            "predictive": self.predictive_engine.get_status(),
            "world_model": self.intelligence.world_model.get_status(),
            "world_model_top_expectations": self.intelligence.world_model.get_top_expectations(limit=10),
            "memory_size": len(self._memory),
            "learning_batch_size": len(self._learning_batch),
            "metrics": {
                "events_processed": self._metrics.events_processed,
                "decisions_made": self._metrics.decisions_made,
                "decisions_successful": self._metrics.decisions_successful,
                "patterns_learned": self._metrics.patterns_learned,
                "predictions_made": self._metrics.predictions_made,
                "confidence": self._metrics.intelligence_confidence,
                "verified_outcomes_learned": self._metrics.verified_outcomes_learned,
            },
        }

    def get_causal_insights(self) -> Dict[str, Any]:
        return self.intelligence.get_causal_insights()

    def get_action_preferences(self) -> Dict[str, Any]:
        return self.intelligence.get_action_preferences()

    def get_learner_report(self) -> Dict[str, Any]:
        return {
            "bandit": self.intelligence.bandit.get_stats(),
            "bayesian": self.intelligence.bayesian.get_stats(),
            "seasonal": self.intelligence.seasonal_detector.get_stats(),
            "adaptive_thresholds": self.intelligence.adaptive_thresholds.get_stats(),
            "predictor": self.intelligence.predictor.get_stats(),
            "world_model": self.intelligence.world_model.get_status(),
            "causal_insights": self.intelligence.get_causal_insights(),
            "action_preferences": self.intelligence.get_action_preferences(),
        }

    def get_world_model(self) -> Dict[str, Any]:
        """v4.0 — Boyd's schwerpunkt exposed."""
        return {
            "status": self.intelligence.world_model.get_status(),
            "expectations": self.intelligence.world_model.get_expectations(),
            "top_expectations": self.intelligence.world_model.get_top_expectations(limit=20),
            "priority_signals": dict(self._observation_priorities),
            "current_volatility": self.intelligence.world_model.compute_volatility(),
        }

    def get_ooda_state(self) -> Dict[str, Any]:
        """v4.0 — Full OODA introspection."""
        return {
            "phase": "running" if self._running else "stopped",
            "current_interval_seconds": self._current_interval,
            "adaptive_range": {
                "min": self.MIN_INTERVAL_SECONDS,
                "base": self.BASE_INTERVAL_SECONDS,
                "max": self.MAX_INTERVAL_SECONDS,
            },
            "world_model": self.intelligence.world_model.get_status(),
            "volatility": self.intelligence.world_model.compute_volatility(),
            "queue_pressure": self._event_queue.qsize() / self.MAX_EVENT_QUEUE_SIZE,
            "priority_signals": dict(sorted(
                self._observation_priorities.items(),
                key=lambda x: x[1],
                reverse=True,
            )[:10]),
            "recent_action_results": list(self._recent_action_results)[-10:],
            "metrics": {
                "world_model_updates": self._metrics.world_model_updates,
                "deviations_detected": self._metrics.deviations_detected,
                "implicit_decisions": self._metrics.implicit_decisions,
                "explicit_decisions": self._metrics.explicit_decisions,
                "adaptive_speedups": self._metrics.adaptive_speedups,
                "orientation_reshapes": self._metrics.orientation_reshapes,
            },
        }


# ═══════════════════════════════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════════════════════════════

_autonomous_loop: Optional[AutonomousLoop] = None
_lock = asyncio.Lock()


async def init_autonomous_loop(
    shared_bus=None,
    meta_engine=None,
    director=None,
    condition_engine=None,
    container=None,
    ai_provider=None,
) -> AutonomousLoop:
    global _autonomous_loop
    async with _lock:
        if _autonomous_loop is None:
            _autonomous_loop = AutonomousLoop(
                shared_bus=shared_bus,
                meta_engine=meta_engine,
                director=director,
                condition_engine=condition_engine,
                container=container,
                ai_provider=ai_provider,
            )
            await _autonomous_loop.start()
    return _autonomous_loop


async def get_autonomous_loop() -> Optional[AutonomousLoop]:
    return _autonomous_loop


async def shutdown_autonomous_loop() -> None:
    global _autonomous_loop
    if _autonomous_loop is not None:
        await _autonomous_loop.stop()
        _autonomous_loop = None


__all__ = [
    "AutonomousLoop",
    "init_autonomous_loop",
    "get_autonomous_loop",
    "shutdown_autonomous_loop",
    "HealthReport",
    "HealthStatus",
    "IntelligenceDecision",
    "Pattern",
    "Prediction",
    "MultiArmedBandit",
    "BayesianNetwork",
    "SeasonalAnomalyDetector",
    "AdaptiveThresholds",
    "ExponentialSmoothingPredictor",
    "WorldModel",
    "Expectation",
    "GuidanceMode",
]
