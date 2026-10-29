"""
Phase B — Decision Gate.

No hand (pipeline publish / scheduler force-post) may run without a
decision_id issued by:
  - Reflex allow (tier-0 / not blocked), or
  - Council approve, or
  - Director auto on trivial risk

Species/master operators may supply decision_id="species_override:<reason>"
for emergency publish.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

logger = logging.getLogger("kanyoza.core.decision_gate")


class DecisionSource(str, Enum):
    REFLEX_ALLOW = "reflex_allow"
    COUNCIL = "council"
    DIRECTOR_AUTO = "director_auto"
    SPECIES_OVERRIDE = "species_override"
    LEGACY = "legacy"  # transition only


@dataclass
class Decision:
    decision_id: str
    source: DecisionSource
    workspace_id: str
    action_name: str
    approved: bool
    correlation_id: str
    risk_level: str = "unknown"
    reason: str = ""
    votes: list = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)
    # Sprint 2 — outcome feedback
    outcome: Optional[str] = None  # success | failure | partial | unknown
    outcome_at: Optional[datetime] = None
    outcome_score: Optional[float] = None
    outcome_source: Optional[str] = None  # bus | engagement | human | plugin
    platform_post_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "source": self.source.value,
            "workspace_id": self.workspace_id,
            "action_name": self.action_name,
            "approved": self.approved,
            "correlation_id": self.correlation_id,
            "risk_level": self.risk_level,
            "reason": self.reason,
            "votes": self.votes,
            "created_at": self.created_at.isoformat(),
            "outcome": self.outcome,
            "outcome_at": self.outcome_at.isoformat() if self.outcome_at else None,
            "outcome_score": self.outcome_score,
            "outcome_source": self.outcome_source,
            "platform_post_id": self.platform_post_id,
            **self.metadata,
        }


class DecisionGateError(PermissionError):
    """Raised when an action lacks a valid decision_id."""


class DecisionGate:
    """In-memory + optional DB registry of decisions for the process."""

    def __init__(self, supabase_client=None) -> None:
        self._decisions: Dict[str, Decision] = {}
        self._db = supabase_client

    def set_supabase(self, client) -> None:
        self._db = client

    def _get_db(self):
        if self._db is not None:
            return self._db
        try:
            from services.supabase import supabase_service
            return supabase_service.service_client or supabase_service.client
        except Exception:
            return None

    def _persist(self, decision: Decision) -> None:
        db = self._get_db()
        if db is None:
            return
        try:
            db.table("decisions").upsert({
                "decision_id": decision.decision_id,
                "workspace_id": decision.workspace_id,
                "source": decision.source.value,
                "action_name": decision.action_name,
                "approved": decision.approved,
                "correlation_id": decision.correlation_id,
                "risk_level": decision.risk_level,
                "reason": decision.reason,
                "votes": decision.votes,
                "metadata": decision.metadata,
                "outcome": decision.outcome,
                "outcome_at": decision.outcome_at.isoformat() if decision.outcome_at else None,
                "outcome_score": decision.outcome_score,
                "outcome_source": decision.outcome_source,
                "platform_post_id": decision.platform_post_id,
                "created_at": decision.created_at.isoformat(),
            }).execute()
        except Exception as exc:
            logger.warning("Decision persistence failed id=%s: %s", decision.decision_id, exc)
            if self._is_production():
                raise RuntimeError("Decision persistence unavailable in production") from exc

    @staticmethod
    def _is_production() -> bool:
        import os
        return (os.getenv("ENVIRONMENT") or os.getenv("ENV") or "development").lower() in ("production", "prod", "staging")

    @staticmethod
    def _from_row(row: dict) -> Decision:
        def dt(v):
            if not v:
                return None
            if isinstance(v, datetime):
                return v
            return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        source = DecisionSource(row.get("source", DecisionSource.LEGACY.value))
        return Decision(
            decision_id=row["decision_id"], source=source, workspace_id=row.get("workspace_id") or "",
            action_name=row.get("action_name") or "", approved=bool(row.get("approved")),
            correlation_id=row.get("correlation_id") or uuid.uuid4().hex, risk_level=row.get("risk_level") or "unknown",
            reason=row.get("reason") or "", votes=row.get("votes") or [], created_at=dt(row.get("created_at")) or datetime.now(timezone.utc),
            metadata=row.get("metadata") or {}, outcome=row.get("outcome"), outcome_at=dt(row.get("outcome_at")),
            outcome_score=row.get("outcome_score"), outcome_source=row.get("outcome_source"), platform_post_id=row.get("platform_post_id"),
        )

    def issue(
        self,
        *,
        source: DecisionSource,
        workspace_id: str,
        action_name: str,
        approved: bool,
        correlation_id: Optional[str] = None,
        risk_level: str = "unknown",
        reason: str = "",
        votes: Optional[list] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Decision:
        decision = Decision(
            decision_id=f"dec_{uuid.uuid4().hex[:16]}",
            source=source,
            workspace_id=workspace_id,
            action_name=action_name,
            approved=approved,
            correlation_id=correlation_id or uuid.uuid4().hex,
            risk_level=risk_level,
            reason=reason,
            votes=votes or [],
            metadata=metadata or {},
        )
        self._decisions[decision.decision_id] = decision
        self._persist(decision)
        # Cap memory
        if len(self._decisions) > 5000:
            for key in list(self._decisions.keys())[:1000]:
                del self._decisions[key]
        logger.info(
            "Decision issued id=%s source=%s approved=%s action=%s ws=%s",
            decision.decision_id,
            source.value,
            approved,
            action_name,
            workspace_id,
        )
        return decision

    def get(self, decision_id: str) -> Optional[Decision]:
        if not decision_id:
            return None
        if decision_id.startswith("species_override:"):
            # Format: species_override:<secret>:<reason>
            # Secret must match SPECIES_OVERRIDE_SECRET env (never trust prefix alone).
            import hmac
            import os
            parts = decision_id.split(":", 2)
            if len(parts) < 3:
                logger.warning("Rejected forgeable species_override (missing secret segment)")
                return None
            _, token, reason = parts[0], parts[1], parts[2]
            expected = (os.getenv("SPECIES_OVERRIDE_SECRET") or "").strip()
            if not expected:
                logger.warning(
                    "Rejected species_override: SPECIES_OVERRIDE_SECRET not configured"
                )
                return None
            if not hmac.compare_digest(token, expected):
                logger.warning("Rejected species_override: invalid secret token")
                return None
            # Prefer already-issued override decisions in registry
            existing = self._decisions.get(decision_id)
            if existing is not None:
                return existing
            decision = Decision(
                decision_id=decision_id,
                source=DecisionSource.SPECIES_OVERRIDE,
                workspace_id="platform",
                action_name="override",
                approved=True,
                correlation_id=uuid.uuid4().hex,
                reason=reason[:200] or "species_override",
                metadata={"override_authenticated": True},
            )
            self._decisions[decision_id] = decision
            logger.info("Species override accepted reason=%s", reason[:80])
            return decision
        cached = self._decisions.get(decision_id)
        if cached is not None:
            return cached
        db = self._get_db()
        if db is not None:
            try:
                res = db.table("decisions").select("*").eq("decision_id", decision_id).limit(1).execute()
                if res.data:
                    decision = self._from_row(res.data[0])
                    self._decisions[decision.decision_id] = decision
                    return decision
            except Exception as exc:
                logger.warning("Decision lookup failed id=%s: %s", decision_id, exc)
                if self._is_production():
                    return None
        return None

    def require_for_publish(
        self,
        decision_id: Optional[str],
        *,
        workspace_id: str,
        strict: bool = True,
    ) -> Decision:
        """
        Validate decision before publish.

        strict=True (Phase B default for new code): missing/invalid/rejected
        raises DecisionGateError.
        """
        if not decision_id:
            if strict:
                raise DecisionGateError(
                    "Publish blocked: missing decision_id "
                    "(Council/Director/Reflex must authorize first)"
                )
            logger.warning(
                "Publish without decision_id (legacy) workspace=%s",
                workspace_id,
            )
            return self.issue(
                source=DecisionSource.LEGACY,
                workspace_id=workspace_id,
                action_name="publish",
                approved=True,
                reason="legacy_no_decision",
            )

        decision = self.get(decision_id)
        if decision is None:
            if strict:
                raise DecisionGateError(
                    f"Publish blocked: unknown decision_id '{decision_id}'"
                )
            logger.warning("Unknown decision_id %s — allowing legacy", decision_id)
            return self.issue(
                source=DecisionSource.LEGACY,
                workspace_id=workspace_id,
                action_name="publish",
                approved=True,
                reason=f"unknown_decision:{decision_id}",
            )

        if not decision.approved:
            raise DecisionGateError(
                f"Publish blocked: decision {decision_id} was not approved "
                f"({decision.reason})"
            )

        # A valid decision is action-scoped.  Possessing an approved decision
        # for another operation must never authorize publishing.
        if decision.source != DecisionSource.SPECIES_OVERRIDE and decision.action_name != "publish":
            raise DecisionGateError(
                f"Publish blocked: decision {decision_id} authorizes "
                f"'{decision.action_name}', not 'publish'"
            )

        if (
            decision.workspace_id not in (workspace_id, "platform")
            and decision.source != DecisionSource.SPECIES_OVERRIDE
        ):
            raise DecisionGateError(
                f"Publish blocked: decision workspace mismatch "
                f"({decision.workspace_id} != {workspace_id})"
            )

        return decision

    def record_outcome(
        self,
        decision_id: Optional[str] = None,
        *,
        correlation_id: Optional[str] = None,
        outcome: str,
        score: Optional[float] = None,
        source: str = "system",
        platform_post_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        workspace_id: Optional[str] = None,
    ) -> Optional[Decision]:
        """Attach real-world outcome to a decision (Sprint 2)."""
        d = None
        if decision_id:
            d = self.get(decision_id)
        if d is None and correlation_id:
            d = self.find_by_correlation(correlation_id, workspace_id=workspace_id)
        if d is None and platform_post_id:
            d = self.find_by_platform_post(platform_post_id, workspace_id=workspace_id)
        if d is None:
            logger.debug(
                "Outcome orphan: decision=%s corr=%s post=%s outcome=%s",
                decision_id, correlation_id, platform_post_id, outcome,
            )
            return None
        d.outcome = outcome
        d.outcome_at = datetime.now(timezone.utc)
        d.outcome_score = score
        d.outcome_source = source
        if platform_post_id:
            d.platform_post_id = platform_post_id
        if metadata:
            d.metadata.update(metadata)
        # Persist the mutated decision. Outcome feedback must survive process
        # restarts; otherwise the learning loop would train on volatile state.
        self._persist(d)
        logger.info(
            "Outcome recorded decision=%s outcome=%s score=%s source=%s",
            d.decision_id, outcome, score, source,
        )
        return d

    def find_by_correlation(
        self, correlation_id: str, *, workspace_id: Optional[str] = None
    ) -> Optional[Decision]:
        if not correlation_id:
            return None
        for d in reversed(list(self._decisions.values())):
            if d.correlation_id == correlation_id and (
                workspace_id is None or d.workspace_id == workspace_id
            ):
                return d
        db = self._get_db()
        if db is not None:
            try:
                query = db.table("decisions").select("*").eq("correlation_id", correlation_id)
                if workspace_id:
                    query = query.eq("workspace_id", workspace_id)
                res = query.order("created_at", desc=True).limit(1).execute()
                if res.data:
                    d = self._from_row(res.data[0])
                    self._decisions[d.decision_id] = d
                    return d
            except Exception as exc:
                logger.warning("Decision correlation lookup failed corr=%s: %s", correlation_id, exc)
        return None

    def find_by_platform_post(
        self, platform_post_id: str, *, workspace_id: Optional[str] = None
    ) -> Optional[Decision]:
        if not platform_post_id:
            return None
        for d in reversed(list(self._decisions.values())):
            if workspace_id is not None and d.workspace_id != workspace_id:
                continue
            if d.platform_post_id == platform_post_id:
                return d
            if (d.metadata or {}).get("platform_post_id") == platform_post_id:
                return d
        db = self._get_db()
        if db is not None:
            try:
                query = db.table("decisions").select("*").eq("platform_post_id", platform_post_id)
                if workspace_id:
                    query = query.eq("workspace_id", workspace_id)
                res = query.order("created_at", desc=True).limit(1).execute()
                if res.data:
                    d = self._from_row(res.data[0])
                    self._decisions[d.decision_id] = d
                    return d
            except Exception as exc:
                logger.warning("Decision post lookup failed post=%s: %s", platform_post_id, exc)
        return None

    def list_for_workspace(self, workspace_id: str, limit: int = 50) -> list:
        if not workspace_id:
            return []
        rows = [d for d in self._decisions.values() if d.workspace_id == workspace_id]
        db = self._get_db()
        if db is not None:
            try:
                res = db.table("decisions").select("*").eq("workspace_id", workspace_id).order(
                    "created_at", desc=True
                ).limit(limit).execute()
                if res.data:
                    rows = [self._from_row(r) for r in res.data]
                    for d in rows:
                        self._decisions[d.decision_id] = d
            except Exception as exc:
                logger.warning("Decision workspace listing failed ws=%s: %s", workspace_id, exc)
        rows.sort(key=lambda x: x.created_at, reverse=True)
        return rows[:limit]



decision_gate = DecisionGate()
