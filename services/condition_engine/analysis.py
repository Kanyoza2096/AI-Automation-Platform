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

from .models import AnomalyType, Baseline, EngineConfig, Observation
from .utils import clamp, day_context, parse_datetime, safe_mean, time_bucket, utc_now
class AnomalyClassifier:
    """Classifies anomalies and generates root cause hypotheses."""
    
    @staticmethod
    def classify(value: float, baseline: Baseline, recent_values: List[float]) -> AnomalyType:
        """Classify anomaly type based on pattern."""
        if len(recent_values) < 3:
            return AnomalyType.OUTLIER
        
        z = baseline.z_score(value)
        recent_z = [baseline.z_score(v) for v in recent_values[-5:]]
        anomalous_count = sum(1 for vz in recent_z if abs(vz) >= 2.0)
        
        if anomalous_count >= 3:
            first_half = recent_values[:len(recent_values)//2]
            second_half = recent_values[len(recent_values)//2:]
            if safe_mean(second_half) > safe_mean(first_half) * 1.3:
                return AnomalyType.LEVEL_SHIFT
            return AnomalyType.DRIFT
        elif anomalous_count == 2:
            return AnomalyType.DRIFT
        else:
            return AnomalyType.SPIKE if z > 0 else AnomalyType.DIP
    
    @staticmethod
    def generate_hypothesis(anomaly_type: AnomalyType, observation: Observation) -> str:
        """Generate root cause hypothesis."""
        if anomaly_type == AnomalyType.SPIKE:
            if observation.shares > observation.likes:
                return "Viral sharing — content resonated strongly"
            elif observation.comments > observation.likes * 0.5:
                return "High discussion — controversial or engaging topic"
            return "Algorithm boost — increased visibility"
        elif anomaly_type == AnomalyType.DIP:
            if observation.impressions < 100:
                return "Low reach — possible algorithm suppression"
            elif observation.engagement_rate < 1.0:
                return "Low engagement rate — content not resonating"
            return "External factor — time/day issue"
        elif anomaly_type == AnomalyType.DRIFT:
            return "Gradual content fatigue or audience shift"
        elif anomaly_type == AnomalyType.LEVEL_SHIFT:
            return "Permanent change — algorithm update or audience shift"
        return "Unknown cause"


# ═══════════════════════════════════════════════════════════════════════════
# PREDICTIVE TREND ANALYZER
# ═══════════════════════════════════════════════════════════════════════════

class PredictiveTrendAnalyzer:
    """Predicts future trend direction and reversal probability."""
    
    @staticmethod
    def predict_next_value(values: List[float], horizon: int = 3) -> float:
        """Predict next value using linear regression."""
        if len(values) < 3:
            return values[-1] if values else 0.0
        
        n = len(values)
        x = list(range(n))
        y = values
        x_mean = safe_mean(x)
        y_mean = safe_mean(y)
        
        numerator = sum((xi - x_mean) * (yi - y_mean) for xi, yi in zip(x, y))
        denominator = sum((xi - x_mean) ** 2 for xi in x)
        
        if denominator == 0:
            return y[-1]
        
        slope = numerator / denominator
        intercept = y_mean - slope * x_mean
        return max(0.0, intercept + slope * (n + horizon - 1))
    
    @staticmethod
    def predict_reversal_probability(values: List[float], direction: str) -> float:
        """Estimate probability of trend reversal."""
        if len(values) < 5:
            return 0.0
        
        recent = values[-5:]
        
        if direction == "rising":
            gains = [recent[i+1] - recent[i] for i in range(len(recent)-1)]
            avg_gain = safe_mean(gains)
            if avg_gain <= 0:
                return 0.7
            return clamp(0.5 - avg_gain / max(recent), 0, 1)
        elif direction == "falling":
            losses = [recent[i] - recent[i+1] for i in range(len(recent)-1)]
            avg_loss = safe_mean(losses)
            if avg_loss <= 0:
                return 0.7
            return clamp(0.5 - avg_loss / max(recent), 0, 1)
        return 0.3


# ═══════════════════════════════════════════════════════════════════════════
# BRAND CLUSTERER
# ═══════════════════════════════════════════════════════════════════════════

class BrandClusterer:
    """Clusters similar brands for shared learning."""
    
    def __init__(self):
        self.brand_features: Dict[str, Dict[str, float]] = {}
        self.clusters: Dict[str, List[str]] = defaultdict(list)
        self._lock = threading.RLock()
    
    def update_brand(self, brand_id: str, features: Dict[str, float]) -> None:
        """Update brand features."""
        with self._lock:
            self.brand_features[brand_id] = features
            self._recluster()
    
    def _recluster(self) -> None:
        """Simple clustering based on feature similarity."""
        self.clusters.clear()
        brands = list(self.brand_features.keys())
        
        if len(brands) < 2:
            return
        
        cluster_ids = list(self.brand_features.values())
        if len(cluster_ids) < 2:
            return
        
        centroid_a = cluster_ids[0]
        centroid_b = cluster_ids[1]
        
        for brand_id, features in self.brand_features.items():
            dist_a = self._distance(features, centroid_a)
            dist_b = self._distance(features, centroid_b)
            cluster = "cluster_a" if dist_a <= dist_b else "cluster_b"
            self.clusters[cluster].append(brand_id)
    
    @staticmethod
    def _distance(a: Dict[str, float], b: Dict[str, float]) -> float:
        """Euclidean distance."""
        keys = set(a.keys()) | set(b.keys())
        return math.sqrt(sum((a.get(k, 0) - b.get(k, 0)) ** 2 for k in keys))
    
    def get_similar_brands(self, brand_id: str) -> List[str]:
        """Get brands in same cluster."""
        with self._lock:
            for cluster_brands in self.clusters.values():
                if brand_id in cluster_brands:
                    return [b for b in cluster_brands if b != brand_id]
            return []
    
    def get_cluster_insights(self, brand_id: str) -> Dict[str, Any]:
        """Get insights from similar brands."""
        similar = self.get_similar_brands(brand_id)
        if not similar:
            return {"similar_brands": 0, "shared_insights": []}
        return {
            "similar_brands": len(similar),
            "shared_insights": [f"Similar brand: {b}" for b in similar[:3]],
        }


# ═══════════════════════════════════════════════════════════════════════════
# ADAPTIVE THRESHOLD MANAGER
# ═══════════════════════════════════════════════════════════════════════════

class AdaptiveThresholdManager:
    """Self-tuning thresholds based on outcomes."""
    
    def __init__(self, config: EngineConfig):
        self.config = config
        self.threshold_history: Dict[str, deque] = defaultdict(lambda: deque(maxlen=100))
        self._lock = threading.RLock()
    
    def update_threshold(self, threshold_name: str, actual_value: float, outcome: str) -> None:
        """Update threshold based on outcome."""
        if not self.config.enable_adaptive_thresholds:
            return
        
        with self._lock:
            current = getattr(self.config, threshold_name, self.config.boost_enter_threshold)
            self.threshold_history[threshold_name].append({
                "threshold": current,
                "actual": actual_value,
                "outcome": outcome,
                "timestamp": utc_now(),
            })
            
            if outcome == "false_positive":
                new_value = current - self.config.threshold_learning_rate
            elif outcome == "true_positive":
                new_value = current + self.config.threshold_learning_rate * 0.5
            elif outcome == "missed":
                new_value = current + self.config.threshold_learning_rate
            else:
                return
            
            new_value = clamp(new_value, self.config.min_threshold, self.config.max_threshold)
            setattr(self.config, threshold_name, new_value)
            
            logger.info("Adaptive threshold %s: %.2f → %.2f (%s)", threshold_name, current, new_value, outcome)


# ═══════════════════════════════════════════════════════════════════════════
# CONTEXT-AWARE LEARNING (v5.1 — NEW)
# ═══════════════════════════════════════════════════════════════════════════

class ContextLearner:
    """
    Learns which (topic, content_type, style, time_bucket) contexts work well.
    
    Enables the engine to:
      - Escalate decisions in unreliable contexts
      - Skip decisions in critically unreliable contexts
      - Blend confidence using global + recent + context rates
      - Learn causal chains of success/failure
    """
    
    def __init__(self, config: EngineConfig):
        self.config = config
        
        # Context: (brand, topic, content_type, style) → {successes, failures}
        self.context_stats: Dict[str, Dict[str, int]] = defaultdict(
            lambda: {"successes": 0, "failures": 0, "total": 0}
        )
        
        # Time: (brand, topic, time_bucket) → {successes, failures}
        self.time_stats: Dict[str, Dict[str, int]] = defaultdict(
            lambda: {"successes": 0, "failures": 0, "total": 0}
        )
        
        # Causal chains: which (action, context) → which outcomes
        self.causal_chains: Dict[str, Deque[Dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=100)
        )
        
        # Global capability stats
        self.capability_stats: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {"successes": 0, "failures": 0, "recent": deque(maxlen=100)}
        )
        
        self._lock = threading.RLock()
    
    def record_outcome(
        self,
        brand_id: str,
        topic: str,
        content_type: str,
        style: str,
        action: str,
        success: bool,
        dt: Optional[datetime] = None,
    ) -> None:
        """Record a decision outcome with full context."""
        if not self.config.enable_context_learning:
            return
        
        dt = dt or utc_now()
        outcome_key = "successes" if success else "failures"
        
        with self._lock:
            # Context stats
            ctx_key = self._context_key(brand_id, topic, content_type, style)
            self.context_stats[ctx_key][outcome_key] += 1
            self.context_stats[ctx_key]["total"] += 1
            
            # Time stats
            tb = time_bucket(dt)
            time_key = self._time_key(brand_id, topic, tb)
            self.time_stats[time_key][outcome_key] += 1
            self.time_stats[time_key]["total"] += 1
            
            # Causal chain
            if self.config.enable_causal_chains:
                self.causal_chains[action].append({
                    "brand_id": brand_id,
                    "topic": topic,
                    "content_type": content_type,
                    "style": style,
                    "time_bucket": tb,
                    "success": success,
                    "timestamp": dt.isoformat(),
                })
            
            # Global capability
            cap = self.capability_stats[action]
            cap[outcome_key] += 1
            cap["recent"].append({
                "success": success,
                "timestamp": dt.isoformat(),
                "context": ctx_key,
            })
    
    def get_context_success_rate(
        self,
        brand_id: str,
        topic: str,
        content_type: str,
        style: str,
    ) -> Optional[float]:
        """Get success rate for this exact context."""
        ctx_key = self._context_key(brand_id, topic, content_type, style)
        stats = self.context_stats.get(ctx_key)
        if not stats:
            return None
        total = stats["successes"] + stats["failures"]
        if total < self.config.min_context_samples:
            return None
        return stats["successes"] / total
    
    def get_time_success_rate(
        self,
        brand_id: str,
        topic: str,
        tb: Optional[str] = None,
    ) -> Optional[float]:
        """Get success rate for this time-of-day bucket."""
        tb = tb or time_bucket()
        time_key = self._time_key(brand_id, topic, tb)
        stats = self.time_stats.get(time_key)
        if not stats:
            return None
        total = stats["successes"] + stats["failures"]
        if total < self.config.min_time_samples:
            return None
        return stats["successes"] / total
    
    def get_recent_success_rate(self, action: str, days: Optional[int] = None) -> Optional[float]:
        """Recency-weighted success rate for an action."""
        days = days or self.config.decay_days
        cutoff = utc_now() - timedelta(days=days)
        
        cap = self.capability_stats.get(action)
        if not cap:
            return None
        
        recent = [
            r for r in cap["recent"]
            if parse_datetime(r["timestamp"]) and parse_datetime(r["timestamp"]) >= cutoff
        ]
        if not recent:
            return None
        
        successes = sum(1 for r in recent if r["success"])
        return successes / len(recent)
    
    def should_escalate(
        self,
        brand_id: str,
        topic: str,
        content_type: str,
        style: str,
        action: str,
    ) -> bool:
        """Escalate to approval if context or time is unreliable."""
        ctx_rate = self.get_context_success_rate(brand_id, topic, content_type, style)
        if ctx_rate is not None and ctx_rate < self.config.unreliable_context_threshold:
            return True
        
        time_rate = self.get_time_success_rate(brand_id, topic)
        if time_rate is not None and time_rate < self.config.unreliable_time_threshold:
            return True
        
        recent_rate = self.get_recent_success_rate(action)
        if recent_rate is not None and recent_rate < self.config.unreliable_context_threshold:
            return True
        
        return False
    
    def should_skip(
        self,
        brand_id: str,
        topic: str,
        content_type: str,
        style: str,
        action: str,
    ) -> bool:
        """Skip if context is critically unreliable."""
        ctx_rate = self.get_context_success_rate(brand_id, topic, content_type, style)
        if ctx_rate is not None and ctx_rate < self.config.critical_skip_threshold:
            return True
        
        time_rate = self.get_time_success_rate(brand_id, topic)
        if time_rate is not None and time_rate < self.config.critical_skip_threshold:
            return True
        
        return False
    
    def adjust_confidence(
        self,
        base_confidence: float,
        brand_id: str,
        topic: str,
        content_type: str,
        style: str,
        action: str,
    ) -> float:
        """Blend base confidence with learned rates."""
        weights = []
        rates = []
        
        # Global
        cap = self.capability_stats.get(action)
        if cap:
            total = cap["successes"] + cap["failures"]
            if total >= 5:
                rates.append(cap["successes"] / total)
                weights.append(0.20)
        
        # Recent
        recent = self.get_recent_success_rate(action)
        if recent is not None:
            rates.append(recent)
            weights.append(0.35)
        
        # Context
        ctx_rate = self.get_context_success_rate(brand_id, topic, content_type, style)
        if ctx_rate is not None:
            rates.append(ctx_rate)
            weights.append(0.25)
        
        # Time
        time_rate = self.get_time_success_rate(brand_id, topic)
        if time_rate is not None:
            rates.append(time_rate)
            weights.append(0.20)
        
        if not rates:
            return base_confidence
        
        total_w = sum(weights)
        learned = sum(r * w for r, w in zip(rates, weights)) / total_w
        
        # Blend with base
        return clamp(base_confidence * 0.3 + learned * 0.7, 0.1, 1.0)
    
    def get_causal_insights(self, action: str, limit: int = 10) -> Dict[str, Any]:
        """Get insights on when this action succeeds/fails."""
        chains = list(self.causal_chains.get(action, []))[-limit:]
        if not chains:
            return {"action": action, "samples": 0, "insights": []}
        
        successes = [c for c in chains if c["success"]]
        failures = [c for c in chains if not c["success"]]
        
        # Which contexts succeed?
        success_topics = [c["topic"] for c in successes if c.get("topic")]
        failure_topics = [c["topic"] for c in failures if c.get("topic")]
        
        # Which time buckets succeed?
        success_times = [c["time_bucket"] for c in successes]
        failure_times = [c["time_bucket"] for c in failures]
        
        insights = []
        if success_topics and failure_topics:
            top_success_topic = max(set(success_topics), key=success_topics.count)
            top_failure_topic = max(set(failure_topics), key=failure_topics.count)
            if top_success_topic != top_failure_topic:
                insights.append(f"Succeeds on topic '{top_success_topic}', fails on '{top_failure_topic}'")
        
        if success_times and failure_times:
            top_success_time = max(set(success_times), key=success_times.count)
            top_failure_time = max(set(failure_times), key=failure_times.count)
            if top_success_time != top_failure_time:
                insights.append(f"Best at '{top_success_time}', worst at '{top_failure_time}'")
        
        return {
            "action": action,
            "samples": len(chains),
            "success_count": len(successes),
            "failure_count": len(failures),
            "success_rate": len(successes) / len(chains) if chains else 0,
            "insights": insights,
        }
    
    def get_learning_report(self) -> Dict[str, Any]:
        """Full learning report."""
        return {
            "contexts_tracked": len(self.context_stats),
            "time_buckets_tracked": len(self.time_stats),
            "causal_chains_tracked": len(self.causal_chains),
            "actions_tracked": len(self.capability_stats),
            "current_time_bucket": time_bucket(),
            "current_day_context": day_context(),
            "top_contexts": sorted(
                [
                    {
                        "context": k,
                        "success_rate": v["successes"] / max(v["total"], 1),
                        "total": v["total"],
                    }
                    for k, v in self.context_stats.items()
                    if v["total"] >= 5
                ],
                key=lambda x: x["success_rate"],
                reverse=True,
            )[:10],
        }
    
    # ─────────────────────────────────────────────────────────────────────
    # KEY HELPERS
    # ─────────────────────────────────────────────────────────────────────
    
    def _context_key(self, brand_id: str, topic: str, content_type: str, style: str) -> str:
        return f"{brand_id}|{topic or 'none'}|{content_type or 'none'}|{style or 'none'}"
    
    def _time_key(self, brand_id: str, topic: str, tb: str) -> str:
        return f"{brand_id}|{topic or 'none'}|{tb}"


# ═══════════════════════════════════════════════════════════════════════════
# COMPLETE AUTONOMOUS CONDITION ENGINE v5.1
# ═══════════════════════════════════════════════════════════════════════════
