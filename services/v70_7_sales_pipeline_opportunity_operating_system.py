"""V70.7 — Sales Pipeline & Opportunity Operating System.

Owns bounded opportunity state, stage transitions, pipeline metrics and routing.
It never performs CRM/provider side effects; consequential work is delegated to
BusinessOperatingLayer and therefore the existing governed execution plane.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Optional
import hashlib, json
from services.v70_business_operating_layer import BusinessOperatingLayer, OperatingRun

class OpportunityStage(str, Enum):
    QUALIFIED = "qualified"
    DISCOVERY = "discovery"
    PROPOSAL = "proposal"
    NEGOTIATION = "negotiation"
    WON = "won"
    LOST = "lost"

class PipelineBlock(str, Enum):
    TENANT_MISMATCH="tenant_mismatch"; UNKNOWN_EVENT="unknown_event"; MISSING_EVENT_ID="missing_event_id"
    MISSING_OPPORTUNITY_ID="missing_opportunity_id"; INVALID_TRANSITION="invalid_transition"
    DUPLICATE_EVENT="duplicate_event"; WORKFLOW_REQUIRED="workflow_required"; TERMINAL="terminal"

@dataclass(frozen=True)
class OpportunitySnapshot:
    workspace_id: str
    opportunity_id: str
    contact_id: str = ""
    contact: str = ""
    amount: float = 0.0
    currency: str = ""
    stage: OpportunityStage = OpportunityStage.QUALIFIED
    probability: int = 20
    expected_close_date: str = ""
    source: str = ""
    version: int = 1
    last_event_id: str = ""

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps({
            "workspace_id":self.workspace_id,"opportunity_id":self.opportunity_id,
            "contact_id":self.contact_id,"contact":self.contact,"amount":self.amount,
            "currency":self.currency,"stage":self.stage.value,"probability":self.probability,
            "expected_close_date":self.expected_close_date,"source":self.source,
            "version":self.version,"last_event_id":self.last_event_id
        }, sort_keys=True).encode()).hexdigest()

@dataclass(frozen=True)
class PipelineMetrics:
    opportunity_count: int
    open_count: int
    won_count: int
    lost_count: int
    open_value: float
    weighted_pipeline: float
    won_value: float

@dataclass(frozen=True)
class PipelineResult:
    workspace_id: str
    opportunity_id: str
    event_id: str
    previous_stage: Optional[OpportunityStage]
    stage: OpportunityStage
    accepted: bool
    workflow_id: Optional[str] = None
    run: Optional[OperatingRun] = None
    reason_codes: tuple[str,...] = ()
    state_version: int = 0
    metrics: Optional[PipelineMetrics] = None

class SalesPipelineOperatingSystem:
    VERSION="v70.7"
    ROUTES={
        "opportunity.created": (None, OpportunityStage.QUALIFIED, "sales.opportunity-create"),
        "opportunity.discovery_started": (OpportunityStage.QUALIFIED, OpportunityStage.DISCOVERY, "sales.opportunity-discovery"),
        "opportunity.proposal_sent": (OpportunityStage.DISCOVERY, OpportunityStage.PROPOSAL, "sales.opportunity-proposal"),
        "opportunity.negotiation_started": (OpportunityStage.PROPOSAL, OpportunityStage.NEGOTIATION, "sales.opportunity-negotiation"),
        "opportunity.won": (None, OpportunityStage.WON, "sales.opportunity-won"),
        "opportunity.lost": (None, OpportunityStage.LOST, "sales.opportunity-lost"),
    }
    TERMINAL=frozenset({OpportunityStage.WON, OpportunityStage.LOST})
    DEFAULT_PROBABILITY={OpportunityStage.QUALIFIED:20,OpportunityStage.DISCOVERY:40,OpportunityStage.PROPOSAL:60,OpportunityStage.NEGOTIATION:80,OpportunityStage.WON:100,OpportunityStage.LOST:0}

    def __init__(self, workspace_id: str, *, operating_layer: BusinessOperatingLayer):
        ws=str(workspace_id or "").strip()
        if not ws or operating_layer is None or operating_layer.workspace_id != ws: raise ValueError("workspace scope mismatch")
        self.workspace_id=ws; self.operating_layer=operating_layer; self._opportunities={}; self._events=set()

    def snapshot(self, opportunity_id: str): return self._opportunities.get(str(opportunity_id).strip())

    def metrics(self)->PipelineMetrics:
        vals=list(self._opportunities.values()); open_vals=[x for x in vals if x.stage not in self.TERMINAL]
        return PipelineMetrics(len(vals),len(open_vals),sum(x.stage==OpportunityStage.WON for x in vals),sum(x.stage==OpportunityStage.LOST for x in vals),sum(x.amount for x in open_vals),sum(x.amount*x.probability/100 for x in open_vals),sum(x.amount for x in vals if x.stage==OpportunityStage.WON))

    def _validate(self,current,expected,target):
        if current is None:
            if expected is not None: raise ValueError(PipelineBlock.INVALID_TRANSITION.value)
            return
        if current.stage in self.TERMINAL: raise ValueError(PipelineBlock.TERMINAL.value)
        if expected is not None and current.stage != expected: raise ValueError(PipelineBlock.INVALID_TRANSITION.value)

    def _next(self,event,target):
        oid=str(event.get("opportunity_id") or event.get("deal_id") or "").strip(); cur=self._opportunities.get(oid)
        p=int(event.get("probability") or self.DEFAULT_PROBABILITY[target]); p=max(0,min(100,p))
        return OpportunitySnapshot(self.workspace_id,oid,str(event.get("contact_id") or (cur.contact_id if cur else "")),str(event.get("contact") or (cur.contact if cur else "")),float(event.get("amount") or (cur.amount if cur else 0) or 0),str(event.get("currency") or (cur.currency if cur else "")),target,p,str(event.get("expected_close_date") or (cur.expected_close_date if cur else "")),str(event.get("source") or (cur.source if cur else "")),(cur.version+1 if cur else 1),str(event.get("event_id")))

    async def process(self, *, event: Mapping[str,Any], objective_id: str, workflow_id: Optional[str]=None, approved: bool=False, now=None)->PipelineResult:
        e=dict(event or {}); eid=str(e.get("event_id") or "").strip()
        if not eid: raise ValueError(PipelineBlock.MISSING_EVENT_ID.value)
        if eid in self._events: raise ValueError(PipelineBlock.DUPLICATE_EVENT.value)
        if str(e.get("workspace_id") or self.workspace_id).strip()!=self.workspace_id: raise ValueError(PipelineBlock.TENANT_MISMATCH.value)
        oid=str(e.get("opportunity_id") or e.get("deal_id") or "").strip()
        if not oid: raise ValueError(PipelineBlock.MISSING_OPPORTUNITY_ID.value)
        name=str(e.get("event_name") or e.get("type") or "").strip(); route=self.ROUTES.get(name)
        if not route: raise ValueError(f"{PipelineBlock.UNKNOWN_EVENT.value}:{name}")
        expected,target,default=route; cur=self._opportunities.get(oid); self._validate(cur,expected,target)
        chosen=str(workflow_id or default).strip()
        if not chosen: raise ValueError(PipelineBlock.WORKFLOW_REQUIRED.value)
        e.update({"workspace_id":self.workspace_id,"opportunity_id":oid,"event_name":name,"stage":target.value})
        run=await self.operating_layer.handle_event(objective_id,chosen,event=e,approved=approved,now=now)
        self._events.add(eid); snap=self._next(e,target); self._opportunities[oid]=snap
        return PipelineResult(self.workspace_id,oid,eid,cur.stage if cur else None,target,run.accepted,chosen,run,tuple(run.admission.reasons),snap.version,self.metrics())
