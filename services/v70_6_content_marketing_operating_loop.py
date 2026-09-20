"""V70.6 Content Intelligence & Marketing Operating Loop.

Business capability layer above the existing content-intelligence, content-engine,
and canonical publishing systems. It owns marketing context/admission, not provider
side effects. AI may interpret evidence; governance remains authoritative for publish.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional


class MarketingDisposition(str, Enum):
    READY = "ready"
    ADVISORY_ONLY = "advisory_only"
    BLOCKED = "blocked"
    INVALID = "invalid"


class ContentLifecycle(str, Enum):
    BRIEF = "brief"
    INTELLIGENCE_READY = "intelligence_ready"
    DRAFT_READY = "draft_ready"
    GOVERNANCE_PENDING = "governance_pending"
    PUBLISHED = "published"
    FAILED = "failed"


@dataclass(frozen=True)
class MarketingObjective:
    objective_id: str
    workspace_id: str
    name: str
    outcome: str
    audience: str = ""
    offer: str = ""
    channels: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContentBrief:
    brief_id: str
    workspace_id: str
    objective_id: str
    topic: str
    audience: str = ""
    offer: str = ""
    channels: tuple[str, ...] = ()
    context: str = ""
    correlation_id: str = ""


@dataclass
class MarketingAdmission:
    disposition: MarketingDisposition
    brief_id: str
    lifecycle: ContentLifecycle
    reason: str
    intelligence_required: bool = True
    publish_authority_required: bool = True
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self) | {
            "disposition": self.disposition.value,
            "lifecycle": self.lifecycle.value,
        }


@dataclass
class ContentCandidate:
    candidate_id: str
    workspace_id: str
    brief_id: str
    topic: str
    caption: str
    channels: tuple[str, ...]
    evidence_fingerprints: tuple[str, ...] = ()
    confidence: float = 0.0
    lifecycle: ContentLifecycle = ContentLifecycle.GOVERNANCE_PENDING
    governance_required: bool = True
    publish_request: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["lifecycle"] = self.lifecycle.value
        return data


class ContentMarketingOperatingLoop:
    """Coordinates marketing work without becoming a second execution kernel."""

    VERSION = "70.6.0"

    def __init__(self, *, action_kernel=None, db=None, workspace_id: str | None = None, production: bool = False):
        self._briefs: Dict[str, ContentBrief] = {}
        self._objectives: Dict[str, MarketingObjective] = {}
        self._candidates: Dict[str, ContentCandidate] = {}
        # Capability layer only; consequential execution is delegated to the
        # existing Action Kernel. No provider/connector is stored here.
        self._action_kernel = action_kernel
        self._db = db
        self._workspace_id = str(workspace_id or "").strip()
        self._production = bool(production)
        if self._production and self._db is None:
            raise RuntimeError("durable marketing database is required in production")

    @staticmethod
    def _fingerprint(*parts: str) -> str:
        raw = "|".join(str(p or "").strip() for p in parts)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    def _get_objective(self, objective_id: str) -> Optional[MarketingObjective]:
        obj = self._objectives.get(objective_id)
        if obj is not None or self._db is None:
            return obj
        result = self._db.table("marketing_objectives").select("*").eq("objective_id", objective_id).limit(1).execute()
        rows = getattr(result, "data", None) or []
        if not rows:
            return None
        row = rows[0]
        obj = MarketingObjective(
            objective_id=str(row["objective_id"]), workspace_id=str(row["workspace_id"]),
            name=str(row["name"]), outcome=str(row["outcome"]), audience=str(row.get("audience") or ""),
            offer=str(row.get("offer") or ""), channels=tuple(row.get("channels") or ()),
            constraints=tuple(row.get("constraints") or ()),
        )
        self._objectives[objective_id] = obj
        return obj

    def _get_brief(self, brief_id: str) -> Optional[ContentBrief]:
        brief = self._briefs.get(brief_id)
        if brief is not None or self._db is None:
            return brief
        result = self._db.table("content_marketing_briefs").select("*").eq("brief_id", brief_id).limit(1).execute()
        rows = getattr(result, "data", None) or []
        if not rows:
            return None
        row = rows[0]
        brief = ContentBrief(
            brief_id=str(row["brief_id"]), workspace_id=str(row["workspace_id"]),
            objective_id=str(row["objective_id"]), topic=str(row["topic"]),
            audience=str(row.get("audience") or ""), offer=str(row.get("offer") or ""),
            channels=tuple(row.get("channels") or ()), context=str(row.get("context") or ""),
            correlation_id=str(row.get("correlation_id") or ""),
        )
        self._briefs[brief_id] = brief
        return brief

    def register_objective(self, objective: MarketingObjective) -> MarketingObjective:
        if not objective.workspace_id or not objective.objective_id:
            raise ValueError("workspace_id and objective_id are required")
        if not objective.name.strip() or not objective.outcome.strip():
            raise ValueError("objective name and outcome are required")
        if self._production and self._workspace_id and objective.workspace_id != self._workspace_id:
            raise ValueError("objective/workspace mismatch")
        if self._db is not None:
            self._db.table("marketing_objectives").upsert({
                "objective_id": objective.objective_id, "workspace_id": objective.workspace_id,
                "name": objective.name, "outcome": objective.outcome, "audience": objective.audience,
                "offer": objective.offer, "channels": list(objective.channels), "constraints": list(objective.constraints),
            }).execute()
        self._objectives[objective.objective_id] = objective
        return objective

    def create_brief(self, brief: ContentBrief) -> ContentBrief:
        if not brief.workspace_id or not brief.brief_id or not brief.objective_id:
            raise ValueError("workspace_id, brief_id and objective_id are required")
        objective = self._get_objective(brief.objective_id)
        if objective and objective.workspace_id != brief.workspace_id:
            raise ValueError("objective/workspace mismatch")
        if not brief.topic.strip():
            raise ValueError("topic is required")
        if self._production and self._workspace_id and brief.workspace_id != self._workspace_id:
            raise ValueError("brief/workspace mismatch")
        if self._db is not None:
            self._db.table("content_marketing_briefs").upsert({
                "brief_id": brief.brief_id, "workspace_id": brief.workspace_id,
                "objective_id": brief.objective_id, "topic": brief.topic, "audience": brief.audience,
                "offer": brief.offer, "channels": list(brief.channels), "context": brief.context,
                "correlation_id": brief.correlation_id,
            }).execute()
        self._briefs[brief.brief_id] = brief
        return brief

    def admit(self, brief_id: str, *, intelligence_available: bool = True,
              channels_supported: bool = True) -> MarketingAdmission:
        brief = self._get_brief(brief_id)
        if not brief:
            return MarketingAdmission(MarketingDisposition.INVALID, brief_id,
                                      ContentLifecycle.BRIEF, "brief_not_found")
        if not intelligence_available:
            return MarketingAdmission(MarketingDisposition.ADVISORY_ONLY, brief_id,
                                      ContentLifecycle.BRIEF,
                                      "content_intelligence_unavailable")
        if not channels_supported or not brief.channels:
            return MarketingAdmission(MarketingDisposition.BLOCKED, brief_id,
                                      ContentLifecycle.BRIEF,
                                      "no_supported_marketing_channel")
        return MarketingAdmission(MarketingDisposition.READY, brief_id,
                                  ContentLifecycle.INTELLIGENCE_READY,
                                  "brief_admitted_for_intelligence")

    def create_candidate(self, brief_id: str, *, caption: str,
                         confidence: float = 0.0,
                         evidence_fingerprints: Optional[List[str]] = None) -> ContentCandidate:
        brief = self._get_brief(brief_id)
        if not brief:
            raise ValueError("brief_not_found")
        if not caption.strip():
            raise ValueError("caption is required")
        candidate_id = f"kdo:marketing:v70.6:{self._fingerprint(brief.workspace_id, brief_id, caption)}"
        candidate = ContentCandidate(
            candidate_id=candidate_id,
            workspace_id=brief.workspace_id,
            brief_id=brief_id,
            topic=brief.topic,
            caption=caption.strip(),
            channels=brief.channels,
            evidence_fingerprints=tuple(evidence_fingerprints or ()),
            confidence=max(0.0, min(1.0, float(confidence))),
        )
        if self._db is not None:
            self._db.table("content_marketing_candidates").upsert({
                "candidate_id": candidate.candidate_id, "workspace_id": candidate.workspace_id,
                "brief_id": candidate.brief_id, "topic": candidate.topic, "caption": candidate.caption,
                "channels": list(candidate.channels), "evidence_fingerprints": list(candidate.evidence_fingerprints),
                "confidence": candidate.confidence, "lifecycle": candidate.lifecycle.value,
                "governance_required": candidate.governance_required, "publish_request": candidate.publish_request,
            }).execute()
        self._candidates[candidate_id] = candidate
        return candidate

    def prepare_publish_request(self, candidate_id: str, *, platform: str,
                                decision_id: str, brand_id: str = "") -> Dict[str, Any]:
        """Prepare a canonical publish intent; does not enqueue or publish."""
        candidate = self._candidates.get(candidate_id)
        if not candidate:
            raise ValueError("candidate_not_found")
        platform = str(platform or "").strip().lower()
        if not platform or platform not in {c.lower() for c in candidate.channels}:
            raise ValueError("platform_not_authorized_by_brief")
        if not decision_id.strip():
            raise ValueError("decision_id is required")
        request = {
            "action": "publish_content",
            "candidate_id": candidate.candidate_id,
            "workspace_id": candidate.workspace_id,
            "brand_id": brand_id,
            "platform": platform,
            "topic": candidate.topic,
            "caption": candidate.caption,
            "decision_id": decision_id.strip(),
            "evidence_fingerprints": list(candidate.evidence_fingerprints),
            "idempotency_key": self._fingerprint(candidate.candidate_id, platform, decision_id),
        }
        candidate.publish_request = request
        if self._db is not None:
            self._db.table("content_marketing_candidates").update({
                "publish_request": request, "updated_at": "now()"
            }).eq("candidate_id", candidate_id).eq("workspace_id", candidate.workspace_id).execute()
        return request

    async def execute_publish(
        self, candidate_id: str, *, platform: str, decision_id: str,
        approved: bool = False, brand_id: str = "", correlation_id: str = "",
        action_kernel=None, dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Execute a prepared marketing publish through the canonical Action Kernel.

        This is the vertical's execution adapter, not a second execution plane.
        It prepares an intent, delegates once to ``publish_content``, and only
        advances the candidate lifecycle from the returned execution outcome.
        """
        request = self.prepare_publish_request(
            candidate_id, platform=platform, decision_id=decision_id, brand_id=brand_id
        )
        kernel = action_kernel or self._action_kernel
        if kernel is None:
            raise RuntimeError("content publish requires the governed Action Kernel")
        result = await kernel.execute(
            workspace_id=request["workspace_id"],
            action_type="publish_content",
            parameters={
                "brand_id": request.get("brand_id") or None,
                "platform": request["platform"],
                "topic": request["topic"],
                "caption": request["caption"],
                "decision_id": request["decision_id"],
                "source": "content_marketing_operating_loop",
                "dry_run": bool(dry_run),
            },
            risk_level="high",
            decision_id=request["decision_id"],
            correlation_id=correlation_id or None,
            idempotency_key=request["idempotency_key"],
            source="content_marketing_operating_loop",
            approved=bool(approved),
        )
        ok = bool(isinstance(result, dict) and result.get("ok"))
        status = str((result or {}).get("status") or (result or {}).get("outcome") or "")
        terminal_success = status in {"published", "sent", "delivered", "read", "finished", "completed"}
        if ok and terminal_success:
            self.reconcile_publish_result(candidate_id, success=True, provider_result=result)
        elif status in {"failed", "rejected", "blocked"}:
            self.reconcile_publish_result(candidate_id, success=False, provider_result=result)
        return result if isinstance(result, dict) else {"ok": False, "status": "invalid_kernel_result"}

    def reconcile_publish_result(self, candidate_id: str, *, success: bool,
                                 provider_result: Optional[Dict[str, Any]] = None) -> ContentCandidate:
        candidate = self._candidates.get(candidate_id)
        if not candidate:
            raise ValueError("candidate_not_found")
        candidate.lifecycle = ContentLifecycle.PUBLISHED if success else ContentLifecycle.FAILED
        if provider_result:
            candidate.publish_request = {**(candidate.publish_request or {}), "provider_result": provider_result}
        if self._db is not None:
            self._db.table("content_marketing_candidates").update({
                "lifecycle": candidate.lifecycle.value, "publish_request": candidate.publish_request,
            }).eq("candidate_id", candidate_id).eq("workspace_id", candidate.workspace_id).execute()
        return candidate

    def get_candidate(self, candidate_id: str) -> Optional[ContentCandidate]:
        candidate = self._candidates.get(candidate_id)
        if candidate is not None or self._db is None:
            return candidate
        result = self._db.table("content_marketing_candidates").select("*").eq("candidate_id", candidate_id).limit(1).execute()
        rows = getattr(result, "data", None) or []
        if not rows:
            return None
        row = rows[0]
        candidate = ContentCandidate(
            candidate_id=str(row["candidate_id"]), workspace_id=str(row["workspace_id"]),
            brief_id=str(row["brief_id"]), topic=str(row["topic"]), caption=str(row["caption"]),
            channels=tuple(row.get("channels") or ()), evidence_fingerprints=tuple(row.get("evidence_fingerprints") or ()),
            confidence=float(row.get("confidence") or 0),
            lifecycle=ContentLifecycle(str(row.get("lifecycle") or ContentLifecycle.GOVERNANCE_PENDING.value)),
            governance_required=bool(row.get("governance_required", True)), publish_request=row.get("publish_request"),
        )
        self._candidates[candidate_id] = candidate
        return candidate


content_marketing_operating_loop = ContentMarketingOperatingLoop()
