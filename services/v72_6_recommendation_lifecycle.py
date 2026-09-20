"""V72.6 — durable recommendation lifecycle and outcome feedback.

This module materializes V72.5 recommendations into an append-only evidence
ledger and supports human lifecycle feedback plus measured-outcome attachment.
It never grants execution authority and never invokes providers, governance,
or the Action Kernel.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol


class RecommendationRepository(Protocol):
    def upsert_recommendation(self, recommendation: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def append_event(self, event: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def get_recommendation(self, workspace_id: str, recommendation_id: str) -> Mapping[str, Any] | None: ...
    def list_recommendations(self, workspace_id: str, limit: int = 50) -> list[Mapping[str, Any]]: ...


class Lifecycle:
    ACTIVE = "ACTIVE"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    DISMISSED = "DISMISSED"
    SUPERSEDED = "SUPERSEDED"


ALLOWED_TRANSITIONS = {
    Lifecycle.ACTIVE: {Lifecycle.ACKNOWLEDGED, Lifecycle.DISMISSED, Lifecycle.SUPERSEDED},
    Lifecycle.ACKNOWLEDGED: {Lifecycle.SUPERSEDED},
    Lifecycle.DISMISSED: {Lifecycle.SUPERSEDED},
    Lifecycle.SUPERSEDED: set(),
}


@dataclass(frozen=True)
class LifecycleResult:
    recommendation_id: str
    workspace_id: str
    lifecycle: str
    event_id: str
    authority_granted: bool = False


class RecommendationLifecycleService:
    VERSION = "v72.6"

    def __init__(self, repository: RecommendationRepository):
        self.repository = repository

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _event_id(recommendation_id: str, event_type: str, actor_id: str, payload: Mapping[str, Any]) -> str:
        import hashlib, json
        raw = json.dumps({"r": recommendation_id, "t": event_type, "a": actor_id, "p": payload}, sort_keys=True, separators=(",", ":"), default=str)
        return "kdo:recommendation-event:v72.6:" + hashlib.sha256(raw.encode()).hexdigest()[:32]

    def materialize(self, surface: Mapping[str, Any]) -> dict[str, Any]:
        """Persist the V72.5 snapshot idempotently; safe for repeated worker runs."""
        workspace_id = str(surface.get("workspaceId") or "").strip()
        if not workspace_id:
            raise ValueError("workspaceId is required")
        persisted = 0
        for rec in surface.get("recommendations") or []:
            row = dict(rec)
            row["workspaceId"] = workspace_id
            row["lifecycle"] = row.get("lifecycle") or Lifecycle.ACTIVE
            row["authorityGranted"] = False
            self.repository.upsert_recommendation(row)
            persisted += 1
        return {"schema": "BusinessIntelligenceRecommendationMaterialization/v1", "version": self.VERSION,
                "workspaceId": workspace_id, "persisted": persisted, "actionAuthorityGranted": False}

    def transition(self, workspace_id: str, recommendation_id: str, lifecycle: str, actor_id: str,
                   reason: str = "") -> LifecycleResult:
        workspace_id = str(workspace_id or "").strip()
        actor_id = str(actor_id or "").strip()
        if not workspace_id or not recommendation_id or not actor_id:
            raise ValueError("workspace_id, recommendation_id and actor_id are required")
        if lifecycle not in ALLOWED_TRANSITIONS:
            raise ValueError("invalid lifecycle")
        current = self.repository.get_recommendation(workspace_id, recommendation_id)
        if current is None:
            raise KeyError("recommendation_not_found")
        current_state = str(current.get("lifecycle") or Lifecycle.ACTIVE)
        if lifecycle not in ALLOWED_TRANSITIONS[current_state]:
            raise ValueError(f"invalid_transition:{current_state}->{lifecycle}")
        payload = {"from": current_state, "to": lifecycle, "reason": reason[:1000]}
        event_id = self._event_id(recommendation_id, "LIFECYCLE_CHANGED", actor_id, payload)
        self.repository.append_event({"eventId": event_id, "workspaceId": workspace_id,
            "recommendationId": recommendation_id, "eventType": "LIFECYCLE_CHANGED",
            "actorId": actor_id, "payload": payload, "createdAt": self._now(), "authorityGranted": False})
        updated = dict(current)
        updated["lifecycle"] = lifecycle
        updated["authorityGranted"] = False
        self.repository.upsert_recommendation(updated)
        return LifecycleResult(recommendation_id, workspace_id, lifecycle, event_id)

    def attach_outcome(self, workspace_id: str, recommendation_id: str, outcome_id: str,
                       actor_id: str) -> LifecycleResult:
        workspace_id = str(workspace_id or "").strip()
        actor_id = str(actor_id or "").strip()
        outcome_id = str(outcome_id or "").strip()
        if not workspace_id or not recommendation_id or not outcome_id or not actor_id:
            raise ValueError("workspace_id, recommendation_id, outcome_id and actor_id are required")
        current = self.repository.get_recommendation(workspace_id, recommendation_id)
        if current is None:
            raise KeyError("recommendation_not_found")
        links = list(current.get("outcomeLinks") or current.get("outcome_links") or [])
        if outcome_id not in links:
            links.append(outcome_id)
        payload = {"outcomeId": outcome_id}
        event_id = self._event_id(recommendation_id, "OUTCOME_ATTACHED", actor_id, payload)
        self.repository.append_event({"eventId": event_id, "workspaceId": workspace_id,
            "recommendationId": recommendation_id, "eventType": "OUTCOME_ATTACHED",
            "actorId": actor_id, "payload": payload, "createdAt": self._now(), "authorityGranted": False})
        updated = dict(current)
        updated["outcomeLinks"] = links
        updated["authorityGranted"] = False
        self.repository.upsert_recommendation(updated)
        return LifecycleResult(recommendation_id, workspace_id, str(current.get("lifecycle") or Lifecycle.ACTIVE), event_id)

    def list(self, workspace_id: str, limit: int = 50) -> dict[str, Any]:
        rows = self.repository.list_recommendations(workspace_id, max(1, min(int(limit), 200)))
        return {"schema": "BusinessIntelligenceRecommendationLedger/v1", "version": self.VERSION,
                "workspaceId": workspace_id, "count": len(rows), "recommendations": [self._safe(r) for r in rows],
                "authority": {"readOnly": True, "actionAuthorityGranted": False,
                              "recommendationsAreDecisions": False}}

    @staticmethod
    def _safe(row: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(row)
        result["authorityGranted"] = False
        result["actionAuthorityGranted"] = False
        return result

class SupabaseRecommendationRepository:
    """Small persistence adapter over the existing Supabase client.

    It is deliberately limited to recommendation/evidence tables; it cannot
    execute business actions or change governance authority.
    """
    def __init__(self, db: Any):
        self.db = db

    @staticmethod
    def _snake(row: Mapping[str, Any]) -> dict[str, Any]:
        mapping = {
            "recommendationId":"recommendation_id", "workspaceId":"workspace_id",
            "objectiveId":"objective_id", "expectedImpact":"expected_impact",
            "capabilityResolution":"capability_resolution", "outcomeLinks":"outcome_links",
            "authorityGranted":"authority_granted", "lastEventId":"last_event_id",
            "outcomeEvaluation":"outcome_evaluation",
        }
        return {mapping.get(k, k): v for k, v in row.items() if k not in {"actionAuthorityGranted"}}

    def upsert_recommendation(self, recommendation: Mapping[str, Any]) -> Mapping[str, Any]:
        row = self._snake(recommendation)
        result = self.db.table("business_intelligence_recommendations").upsert(row, on_conflict="recommendation_id").execute()
        return (getattr(result, "data", None) or [row])[0]

    def append_event(self, event: Mapping[str, Any]) -> Mapping[str, Any]:
        row = {"event_id": event["eventId"], "workspace_id": event["workspaceId"],
               "recommendation_id": event["recommendationId"], "event_type": event["eventType"],
               "actor_id": event["actorId"], "payload": event.get("payload") or {},
               "created_at": event.get("createdAt"), "authority_granted": False}
        result = self.db.table("business_intelligence_recommendation_events").upsert(row, on_conflict="event_id").execute()
        return (getattr(result, "data", None) or [row])[0]

    def get_recommendation(self, workspace_id: str, recommendation_id: str) -> Mapping[str, Any] | None:
        result = (self.db.table("business_intelligence_recommendations").select("*")
                  .eq("workspace_id", workspace_id).eq("recommendation_id", recommendation_id).limit(1).execute())
        return (getattr(result, "data", None) or [None])[0]

    def list_recommendations(self, workspace_id: str, limit: int = 50) -> list[Mapping[str, Any]]:
        result = (self.db.table("business_intelligence_recommendations").select("*")
                  .eq("workspace_id", workspace_id).order("updated_at", desc=True).limit(limit).execute())
        return list(getattr(result, "data", None) or [])
