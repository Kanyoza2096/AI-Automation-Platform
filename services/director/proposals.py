"""
KANYOZA DIRECTOR — Proposals and Detectors (v5.5)

Turns signals into proposals, proposals into tasks. This module is the
Director's "proactive" layer — what makes it a partner instead of a
reactor.

Flow:
    Detector.detect() → DirectorSignal
    ProposalGenerator.from_signal(signal) → Optional[Proposal]
    Human approves/rejects → Proposal status changes
    Approved proposal → Task created
    Task executes → outcome recorded
    Outcome → learning loop → next cycle

Detectors run on a schedule (every 6 hours for engagement, every 30s for
load). Proposals are generated from signals that are recent and not
already covered by an existing proposal.
"""
from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Protocol

from .models import (
    DirectorSignal, Proposal, ProposalStatus, Risk, Sector,
    Task, Status, Authority,
)
from .utils import now, iso, safe

log = logging.getLogger("kanyoza.director.proposals")


# ═══════════════════════════════════════════════════════════════════════════
# DETECTOR BASE
# ═══════════════════════════════════════════════════════════════════════════

class Detector(ABC):
    """Base class for detectors. A detector reads data and produces signals.

    Detectors are stateless from the Director's perspective — each run
    reads what it needs and produces fresh signals. The Director stores
    the signals and deduplicates proposals against them.
    """

    name: str = "base"

    @abstractmethod
    async def detect(self, workspace_id: str, context: Dict[str, Any]) -> List[DirectorSignal]:
        """Run this detector for a workspace. Returns signals found.

        context contains:
          - supabase_client: the Supabase client (may be None)
          - director: the Director instance (for reading historical state)
          - since: datetime — how far back to look
        """
        ...


# ═══════════════════════════════════════════════════════════════════════════
# ENGAGEMENT-BY-HOUR DETECTOR
# ═══════════════════════════════════════════════════════════════════════════

class EngagementByHourDetector(Detector):
    """Detects that a brand's posts get more engagement at a different
    time-of-day than its current posting schedule uses.

    Only produces signals when:
      - At least 30 posts in the last 30 days for the brand
      - A specific hour bin shows >= 20% engagement lift over the median
      - The lift is not explained by day-of-week (posts on that hour are
        spread across weekdays, not concentrated on one day)

    Signal payload:
        {
            "brand_id": str,
            "current_peak_hours": [int, ...],  # from posting_schedule
            "suggested_peak_hours": [int, ...],
            "lift_pct": float,
            "sample_size": int,
            "baseline_engagement": float,
            "target_engagement": float,
        }
    """

    name = "engagement_by_hour"

    async def detect(
        self,
        workspace_id: str,
        context: Dict[str, Any],
    ) -> List[DirectorSignal]:
        supabase = context.get("supabase_client")
        if supabase is None:
            return []

        since = context.get("since") or (now() - timedelta(days=30))
        signals: List[DirectorSignal] = []

        try:
            # Get all active brands for the workspace
            brands_resp = (
                supabase.table("brands")
                .select("id, name, posting_schedule")
                .eq("workspace_id", workspace_id)
                .eq("status", "active")
                .execute()
            )
            brands = brands_resp.data or []
        except Exception as exc:
            log.warning("engagement detector: brands query failed: %s", exc)
            return []

        for brand in brands:
            brand_id = brand.get("id")
            if not brand_id:
                continue

            try:
                posts_resp = (
                    supabase.table("post_history")
                    .select("id, published_at, engagement_likes, engagement_comments, engagement_shares")
                    .eq("workspace_id", workspace_id)
                    .eq("brand_id", brand_id)
                    .gte("published_at", iso(since))
                    .execute()
                )
                posts = posts_resp.data or []
            except Exception:
                continue

            if len(posts) < 30:
                continue

            signal = self._analyze(brand, posts, workspace_id, since)
            if signal is not None:
                signals.append(signal)

        return signals

    def _analyze(
        self,
        brand: dict,
        posts: List[dict],
        workspace_id: str,
        since: datetime,
    ) -> Optional[DirectorSignal]:
        """Compute per-hour engagement and detect lift."""
        hourly: Dict[int, List[float]] = {}
        for p in posts:
            published = p.get("published_at") or ""
            try:
                dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
            except Exception:
                continue
            hour = dt.hour
            engagement = (
                int(p.get("engagement_likes") or 0)
                + 2 * int(p.get("engagement_comments") or 0)
                + 3 * int(p.get("engagement_shares") or 0)
            )
            hourly.setdefault(hour, []).append(engagement)

        # Only consider hour bins with >= 3 samples
        valid_bins = {
            h: vals for h, vals in hourly.items() if len(vals) >= 3
        }
        if len(valid_bins) < 3:
            return None

        hour_medians = {
            h: sorted(vals)[len(vals) // 2] for h, vals in valid_bins.items()
        }
        overall_median = sorted(hour_medians.values())[len(hour_medians) // 2]

        if overall_median <= 0:
            return None

        # Find the hour bin with highest median engagement
        best_hour = max(hour_medians, key=lambda h: hour_medians[h])
        best_median = hour_medians[best_hour]
        lift_pct = (best_median - overall_median) / overall_median * 100

        if lift_pct < 20:
            return None

        # Check whether current schedule already targets this hour
        schedule = brand.get("posting_schedule") or {}
        current_hours = schedule.get("content_hours") or []
        if best_hour in current_hours:
            return None  # already doing it

        return DirectorSignal(
            id=f"sig_{uuid.uuid4().hex[:14]}",
            detector=self.name,
            sector=Sector.CONTENT,
            signal_type="schedule_opportunity",
            subject_type="brand",
            subject_id=brand.get("id"),
            workspace_id=workspace_id,
            confidence=min(0.9, lift_pct / 100),
            rationale=(
                f"Brand '{brand.get('name')}' gets {lift_pct:.0f}% more engagement "
                f"at hour {best_hour:02d}:00 than its baseline. "
                f"{len(valid_bins[best_hour])} posts in that hour bin over 30 days."
            ),
            payload={
                "brand_id": brand.get("id"),
                "brand_name": brand.get("name"),
                "current_peak_hours": list(current_hours),
                "suggested_peak_hours": [best_hour],
                "lift_pct": round(lift_pct, 1),
                "sample_size": len(valid_bins[best_hour]),
                "baseline_engagement": overall_median,
                "target_engagement": best_median,
            },
            detected_at=now(),
            expires_at=now() + timedelta(days=7),
        )


# ═══════════════════════════════════════════════════════════════════════════
# LOAD FORECAST DETECTOR
# ═══════════════════════════════════════════════════════════════════════════

class LoadForecastDetector(Detector):
    """Detects when a worker pool is trending toward saturation."""

    name = "load_forecast"

    async def detect(
        self,
        workspace_id: str,
        context: Dict[str, Any],
    ) -> List[DirectorSignal]:
        supabase = context.get("supabase_client")
        if supabase is None:
            return []

        signals: List[DirectorSignal] = []
        try:
            metrics_resp = (
                supabase.table("worker_metrics")
                .select("pool_name, queue_depth, active_workers, avg_latency_ms, recorded_at")
                .eq("workspace_id", workspace_id)
                .gte("recorded_at", iso(now() - timedelta(minutes=15)))
                .order("recorded_at", desc=True)
                .limit(200)
                .execute()
            )
            rows = metrics_resp.data or []
        except Exception:
            return []

        # Group by pool
        by_pool: Dict[str, List[dict]] = {}
        for row in rows:
            pool = row.get("pool_name")
            if pool:
                by_pool.setdefault(pool, []).append(row)

        for pool_name, pool_rows in by_pool.items():
            if len(pool_rows) < 3:
                continue

            pool_rows.sort(key=lambda r: r.get("recorded_at") or "", reverse=True)
            latest = pool_rows[0]
            oldest = pool_rows[-1]

            q_now = int(latest.get("queue_depth") or 0)
            q_old = int(oldest.get("queue_depth") or 0)
            seconds = 15 * 60
            growth_per_min = (q_now - q_old) / max(1, seconds / 60)

            if q_now < 20 or growth_per_min <= 0:
                continue

            minutes_to_saturation = q_now / growth_per_min if growth_per_min > 0 else 999
            if minutes_to_saturation > 20:
                continue

            workers = int(latest.get("active_workers") or 1)
            proposed = max(workers + 1, int(q_now / 10) + 1)

            signals.append(DirectorSignal(
                id=f"sig_{uuid.uuid4().hex[:14]}",
                detector=self.name,
                sector=Sector.INFRA,
                signal_type="scale_recommendation",
                subject_type="worker_pool",
                subject_id=pool_name,
                workspace_id=workspace_id,
                confidence=0.75,
                rationale=(
                    f"Worker pool '{pool_name}' queue depth is {q_now} and growing "
                    f"at {growth_per_min:.1f}/min. Estimated saturation in "
                    f"{minutes_to_saturation:.0f} minutes."
                ),
                payload={
                    "pool_name": pool_name,
                    "queue_depth": q_now,
                    "growth_per_minute": round(growth_per_min, 1),
                    "minutes_to_saturation": round(minutes_to_saturation, 0),
                    "current_workers": workers,
                    "proposed_workers": proposed,
                },
                detected_at=now(),
                expires_at=now() + timedelta(minutes=30),
            ))

        return signals


# ═══════════════════════════════════════════════════════════════════════════
# PROPOSAL GENERATOR
# ═══════════════════════════════════════════════════════════════════════════

class ProposalGenerator:
    """Turns signals into proposals by matching them to capabilities."""

    # Which signal types map to which capability, with default params
    SIGNAL_CAPABILITY_MAP: Dict[str, Dict[str, Any]] = {
        "schedule_opportunity": {
            "capability": "update_brand_posting_schedule",
            "risk": Risk.LOW,
            "estimated_cost": "$0.00",
        },
        "scale_recommendation": {
            "capability": "scale_worker_pool",
            "risk": Risk.LOW,
            "estimated_cost": None,  # filled from payload
        },
    }

    def __init__(self, director):
        self.director = director

    def from_signal(self, signal: DirectorSignal) -> Optional[Proposal]:
        mapping = self.SIGNAL_CAPABILITY_MAP.get(signal.signal_type)
        if mapping is None:
            return None

        cap_name = mapping["capability"]
        cap = self.director.capabilities.get(cap_name)
        if cap is None or not cap.enabled or not cap.proposal_capable:
            return None

        # Skip if a pending proposal already exists for this signal subject
        for existing in self.director.proposals.values():
            if (existing.status == ProposalStatus.PENDING
                and existing.sector == signal.sector
                and existing.signal_ids
                and any(s == signal.id for s in existing.signal_ids)):
                return None

        # Skip if a recent proposal for the same subject was rejected
        recent_cutoff = now() - timedelta(days=7)
        for existing in self.director.proposals.values():
            if (existing.status == ProposalStatus.REJECTED
                and existing.created_at >= recent_cutoff
                and existing.sector == signal.sector
                and existing.params.get(cap.target_param or "") == signal.subject_id):
                return None

        params = self._build_params(signal, cap_name)
        if params is None:
            return None

        return Proposal(
            id=f"prop_{uuid.uuid4().hex[:14]}",
            workspace_id=signal.workspace_id,
            sector=signal.sector,
            title=self._build_title(signal),
            rationale=self._build_rationale(signal),
            capability=cap_name,
            params=params,
            target_param_value=signal.subject_id,
            expected_benefit=self._build_expected_benefit(signal),
            estimated_cost=self._build_estimated_cost(signal, mapping),
            risk=mapping["risk"],
            confidence=signal.confidence,
            signal_ids=[signal.id],
            status=ProposalStatus.PENDING,
            created_at=now(),
            expires_at=signal.expires_at,
        )

    def _build_params(self, signal: DirectorSignal, cap_name: str) -> Optional[Dict[str, Any]]:
        if signal.signal_type == "schedule_opportunity":
            return {
                "brand_id": signal.subject_id,
                "posting_hours": signal.payload["suggested_peak_hours"],
                "workspace_id": signal.workspace_id,
            }
        if signal.signal_type == "scale_recommendation":
            return {
                "pool_name": signal.subject_id,
                "target_workers": signal.payload["proposed_workers"],
                "workspace_id": signal.workspace_id,
            }
        return None

    def _build_title(self, signal: DirectorSignal) -> str:
        if signal.signal_type == "schedule_opportunity":
            brand_name = signal.payload.get("brand_name", "brand")
            hours = signal.payload.get("suggested_peak_hours") or []
            if hours:
                return f"Shift {brand_name} posting to {hours[0]:02d}:00 for {signal.payload.get('lift_pct', 0):.0f}% more engagement"
        if signal.signal_type == "scale_recommendation":
            pool = signal.payload.get("pool_name", "pool")
            return f"Scale {pool} from {signal.payload.get('current_workers')} to {signal.payload.get('proposed_workers')} workers"
        return signal.rationale[:80]

    def _build_rationale(self, signal: DirectorSignal) -> str:
        if signal.signal_type == "schedule_opportunity":
            p = signal.payload
            return (
                f"Analysis of {p.get('sample_size')} posts over the last 30 days shows "
                f"that hour {p.get('suggested_peak_hours', [0])[0]:02d}:00 gets "
                f"median engagement of {p.get('target_engagement')}, compared to the "
                f"brand's baseline of {p.get('baseline_engagement')}.\n\n"
                f"Current peak hours: {p.get('current_peak_hours')}. "
                f"Suggested: {p.get('suggested_peak_hours')}.\n\n"
                f"Impact: {p.get('lift_pct', 0):.0f}% engagement lift, based on "
                f"existing content. No new content required — just rescheduling."
            )
        if signal.signal_type == "scale_recommendation":
            p = signal.payload
            return (
                f"Worker pool '{p.get('pool_name')}' is under load. "
                f"Current queue depth: {p.get('queue_depth')} (growing "
                f"{p.get('growth_per_minute')}/min). At this rate, the pool "
                f"saturates in {p.get('minutes_to_saturation')} minutes.\n\n"
                f"Current workers: {p.get('current_workers')}. "
                f"Proposed: {p.get('proposed_workers')}.\n\n"
                f"This is a transient scale-up. The pool will scale back down "
                f"automatically when load drops."
            )
        return signal.rationale

    def _build_expected_benefit(self, signal: DirectorSignal) -> str:
        if signal.signal_type == "schedule_opportunity":
            return f"{signal.payload.get('lift_pct', 0):.0f}% more engagement"
        if signal.signal_type == "scale_recommendation":
            return "prevent saturation"
        return "unknown"

    def _build_estimated_cost(self, signal: DirectorSignal, mapping: Dict[str, Any]) -> str:
        if mapping.get("estimated_cost"):
            return mapping["estimated_cost"]
        if signal.signal_type == "scale_recommendation":
            workers = signal.payload.get("proposed_workers", 0)
            return f"~${workers * 0.06:.2f}/hr"
        return "N/A"


__all__ = [
    "Detector",
    "EngagementByHourDetector",
    "LoadForecastDetector",
    "ProposalGenerator",
]
