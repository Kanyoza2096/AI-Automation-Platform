"""V70.2 — Customer & Sales Operating Loop.

Turns trusted customer/lead events into a bounded, stateful sales operating loop.
The module owns business state and routing only. Consequential work is delegated to
BusinessOperatingLayer, which converges into the existing governed automation path.
It never calls CRM/provider APIs or the Action Kernel directly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional
import hashlib
import json
import uuid
import asyncio
from datetime import datetime, timezone

from services.v70_business_operating_layer import BusinessOperatingLayer, OperatingRun


class LeadState(str, Enum):
    NEW = "new"
    QUALIFIED = "qualified"
    FOLLOW_UP_DUE = "follow_up_due"
    ENGAGED = "engaged"
    WON = "won"
    LOST = "lost"
    OPTED_OUT = "opted_out"


class SalesLoopBlock(str, Enum):
    TENANT_MISMATCH = "tenant_mismatch"
    UNKNOWN_EVENT = "unknown_event"
    MISSING_LEAD_ID = "missing_lead_id"
    MISSING_EVENT_ID = "missing_event_id"
    INVALID_TRANSITION = "invalid_transition"
    OBJECTIVE_REQUIRED = "objective_required"
    WORKFLOW_REQUIRED = "workflow_required"
    DUPLICATE_EVENT = "duplicate_event"
    OPTED_OUT = "opted_out"


@dataclass(frozen=True)
class LeadSnapshot:
    workspace_id: str
    lead_id: str
    state: LeadState
    name: str = ""
    email: str = ""
    phone: str = ""
    company: str = ""
    amount: float = 0.0
    version: int = 1
    last_event_id: str = ""
    updated_by_event: str = ""

    def fingerprint(self) -> str:
        payload = {
            "workspace_id": self.workspace_id, "lead_id": self.lead_id,
            "state": self.state.value, "name": self.name, "email": self.email,
            "phone": self.phone, "company": self.company, "amount": self.amount,
            "version": self.version, "last_event_id": self.last_event_id,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class SalesLoopResult:
    workspace_id: str
    lead_id: str
    event_id: str
    previous_state: Optional[LeadState]
    state: Optional[LeadState]
    accepted: bool
    workflow_id: Optional[str] = None
    run: Optional[OperatingRun] = None
    reason_codes: tuple[str, ...] = ()
    state_version: int = 0
    authority: str = "none"
    execution_effect: str = "none"

    def __post_init__(self) -> None:
        if self.authority != "none" or self.execution_effect != "none":
            raise ValueError("sales loop result cannot grant authority")


class CustomerSalesOperatingLoop:
    """Tenant-bound state machine above the governed business operating layer."""

    VERSION = "v70.2"

    EVENT_ROUTES = {
        "lead.created": (None, LeadState.NEW, "sales.new-lead-capture"),
        "lead.qualified": (LeadState.NEW, LeadState.QUALIFIED, "sales.qualified-lead-status"),
        "lead.follow_up_due": (LeadState.QUALIFIED, LeadState.FOLLOW_UP_DUE, "sales.follow-up-due"),
        "customer.engaged": (LeadState.FOLLOW_UP_DUE, LeadState.ENGAGED, "sales.follow-up-due"),
        "deal.won": (None, LeadState.WON, "sales.deal-won"),
        "deal.lost": (None, LeadState.LOST, "sales.deal-lost"),
        "customer.opted_out": (None, LeadState.OPTED_OUT, "sales.customer-opt-out"),
    }

    TERMINAL = frozenset({LeadState.WON, LeadState.LOST, LeadState.OPTED_OUT})

    def __init__(self, workspace_id: str, *, operating_layer: BusinessOperatingLayer, db=None, production: bool = False, worker_id: str | None = None):
        ws = str(workspace_id or "").strip()
        if not ws:
            raise ValueError("workspace_id is required")
        if operating_layer is None or operating_layer.workspace_id != ws:
            raise ValueError("operating layer workspace mismatch")
        self.workspace_id = ws
        self.operating_layer = operating_layer
        self.db = db
        self.production = bool(production)
        self.worker_id = str(worker_id or f"sales-{uuid.uuid4().hex[:12]}")
        if self.production and self.db is None:
            raise RuntimeError("durable sales database is required in production")
        self._claims: dict[str, dict[str, str]] = {}
        self._lead_claims: dict[str, dict[str, str]] = {}
        self._leads: dict[str, LeadSnapshot] = {}
        self._events: set[str] = set()

    async def _rpc(self, name: str, params: dict[str, Any]):
        if self.db is None:
            return None
        return await asyncio.to_thread(lambda: self.db.rpc(name, params).execute())

    async def _hydrate_lead(self, lead_id: str) -> Optional[LeadSnapshot]:
        if self.db is None:
            return self._leads.get(lead_id)
        result = await asyncio.to_thread(lambda: self.db.table("v70_sales_lead_state").select("*").eq("workspace_id", self.workspace_id).eq("lead_id", lead_id).limit(1).execute())
        rows = getattr(result, "data", None) or []
        if not rows:
            return None
        row = rows[0]
        snap = LeadSnapshot(
            workspace_id=self.workspace_id, lead_id=str(row["lead_id"]),
            state=LeadState(str(row["state"])), name=str(row.get("name") or ""),
            email=str(row.get("email") or ""), phone=str(row.get("phone") or ""),
            company=str(row.get("company") or ""), amount=float(row.get("amount") or 0),
            version=int(row.get("version") or 1), last_event_id=str(row.get("last_event_id") or ""),
            updated_by_event=str(row.get("updated_by_event") or ""),
        )
        self._leads[lead_id] = snap
        return snap

    async def _claim_event(self, event_id: str, lead_id: str, event_name: str) -> None:
        if self.db is None:
            if event_id in self._events:
                raise ValueError(SalesLoopBlock.DUPLICATE_EVENT.value)
            return
        # Identity fence is durable. A duplicate completed/processing event is never replayed blindly.
        try:
            await asyncio.to_thread(lambda: self.db.table("v70_sales_lead_event_fence").insert({
                "workspace_id": self.workspace_id, "event_id": event_id, "lead_id": lead_id, "event_name": event_name
            }).execute())
        except Exception:
            existing = await asyncio.to_thread(lambda: self.db.table("v70_sales_lead_event_fence").select("status").eq("workspace_id", self.workspace_id).eq("event_id", event_id).limit(1).execute())
            rows = getattr(existing, "data", None) or []
            if rows and str(rows[0].get("status") or "received") in {"completed", "processing", "uncertain", "failed"}:
                raise ValueError(SalesLoopBlock.DUPLICATE_EVENT.value)
            if not rows:
                raise
        result = await self._rpc("claim_v70_sales_event", {
            "p_workspace_id": self.workspace_id, "p_event_id": event_id, "p_worker": self.worker_id, "p_lease_seconds": 300
        })
        rows = getattr(result, "data", None) or []
        if not rows:
            raise ValueError(SalesLoopBlock.DUPLICATE_EVENT.value)
        row = rows[0]
        self._claims[event_id] = {"claim_token": str(row["claim_token"]), "claim_owner": self.worker_id}

    async def _claim_lead(self, lead_id: str) -> None:
        if self.db is None:
            return
        result = await self._rpc("claim_v70_sales_lead_execution", {
            "p_workspace_id": self.workspace_id, "p_lead_id": lead_id, "p_worker": self.worker_id, "p_lease_seconds": 300
        })
        rows = getattr(result, "data", None) or []
        if not rows:
            raise ValueError("sales_lead_execution_busy")
        row = rows[0]
        self._lead_claims[lead_id] = {"claim_token": str(row["claim_token"]), "claim_owner": self.worker_id}

    async def _release_lead(self, lead_id: str) -> None:
        if self.db is None:
            return
        claim = self._lead_claims.pop(lead_id, None)
        if claim:
            await self._rpc("release_v70_sales_lead_execution", {
                "p_workspace_id": self.workspace_id, "p_lead_id": lead_id, "p_claim_token": claim["claim_token"]
            })

    async def _finalize_event(self, event_id: str, status: str, result_json: dict[str, Any] | None = None, error: str | None = None) -> None:
        if self.db is None:
            return
        claim = self._claims.pop(event_id, None)
        if not claim:
            return
        await self._rpc("finalize_v70_sales_event", {
            "p_workspace_id": self.workspace_id, "p_event_id": event_id,
            "p_claim_token": claim["claim_token"], "p_status": status,
            "p_result_json": result_json, "p_error": (error or "")[:2000] or None
        })

    async def recover_expired_events(self, limit: int = 100) -> int:
        if self.db is None:
            return 0
        result = await self._rpc("mark_expired_v70_sales_events_uncertain", {
            "p_workspace_id": self.workspace_id, "p_limit": max(1, min(int(limit), 1000))
        })
        data = getattr(result, "data", None)
        if isinstance(data, int):
            return data
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return int(data[0].get("count") or data[0].get("mark_expired_v70_sales_events_uncertain") or 0)
        return 0

    async def _persist_state(self, snapshot: LeadSnapshot, previous: Optional[LeadSnapshot]) -> None:
        if self.db is None:
            self._leads[snapshot.lead_id] = snapshot
            return
        if previous is None:
            try:
                await asyncio.to_thread(lambda: self.db.table("v70_sales_lead_state").insert({
                    "workspace_id": self.workspace_id, "lead_id": snapshot.lead_id, "state": snapshot.state.value,
                    "name": snapshot.name, "email": snapshot.email, "phone": snapshot.phone, "company": snapshot.company,
                    "amount": snapshot.amount, "version": snapshot.version, "last_event_id": snapshot.last_event_id,
                    "updated_by_event": snapshot.updated_by_event, "authority_granted": False, "execution_effect": "none"
                }).execute())
                self._leads[snapshot.lead_id] = snapshot
                return
            except Exception:
                previous = await self._hydrate_lead(snapshot.lead_id)
                if previous is None:
                    raise
        result = await self._rpc("transition_v70_sales_lead", {
            "p_workspace_id": self.workspace_id, "p_lead_id": snapshot.lead_id,
            "p_expected_version": previous.version, "p_state": snapshot.state.value,
            "p_name": snapshot.name, "p_email": snapshot.email, "p_phone": snapshot.phone,
            "p_company": snapshot.company, "p_amount": snapshot.amount, "p_event_id": snapshot.last_event_id,
            "p_event_name": snapshot.updated_by_event
        })
        rows = getattr(result, "data", None) or []
        if not rows:
            raise RuntimeError("sales lead state version conflict")
        self._leads[snapshot.lead_id] = snapshot

    def snapshot(self, lead_id: str) -> Optional[LeadSnapshot]:
        return self._leads.get(str(lead_id).strip())

    def _route(self, event_name: str) -> tuple[Optional[LeadState], LeadState, str]:
        route = self.EVENT_ROUTES.get(str(event_name or "").strip())
        if route is None:
            raise ValueError(f"{SalesLoopBlock.UNKNOWN_EVENT.value}:{event_name}")
        return route

    def _validate_transition(self, current: Optional[LeadSnapshot], expected: Optional[LeadState], target: LeadState) -> None:
        if current is None:
            if expected is not None:
                raise ValueError(SalesLoopBlock.INVALID_TRANSITION.value)
            return
        if current.state == LeadState.OPTED_OUT and target != LeadState.OPTED_OUT:
            raise ValueError(SalesLoopBlock.OPTED_OUT.value)
        if expected is not None and current.state != expected:
            raise ValueError(SalesLoopBlock.INVALID_TRANSITION.value)
        if current.state in self.TERMINAL and target != current.state:
            raise ValueError(SalesLoopBlock.INVALID_TRANSITION.value)

    @staticmethod
    def _lead_id(event: Mapping[str, Any]) -> str:
        return str(event.get("lead_id") or event.get("contact_id") or event.get("customer_id") or "").strip()

    def _next_snapshot(self, event: Mapping[str, Any], target: LeadState) -> LeadSnapshot:
        lead_id = self._lead_id(event)
        current = self._leads.get(lead_id)
        return LeadSnapshot(
            workspace_id=self.workspace_id,
            lead_id=lead_id,
            state=target,
            name=str(event.get("name") or (current.name if current else "")),
            email=str(event.get("email") or (current.email if current else "")),
            phone=str(event.get("phone") or (current.phone if current else "")),
            company=str(event.get("company") or (current.company if current else "")),
            amount=float(event.get("amount") or (current.amount if current else 0.0) or 0.0),
            version=(current.version + 1 if current else 1),
            last_event_id=str(event.get("event_id")),
            updated_by_event=str(event.get("event_name") or ""),
        )

    async def process(self, *, event: Mapping[str, Any], objective_id: str,
                      workflow_id: Optional[str] = None, approved: bool = False,
                      now=None) -> SalesLoopResult:
        payload = dict(event or {})
        event_id = str(payload.get("event_id") or "").strip()
        if not event_id:
            raise ValueError(SalesLoopBlock.MISSING_EVENT_ID.value)
        if self.db is None and event_id in self._events:
            raise ValueError(SalesLoopBlock.DUPLICATE_EVENT.value)
        event_ws = str(payload.get("workspace_id") or self.workspace_id).strip()
        if event_ws != self.workspace_id:
            raise ValueError(SalesLoopBlock.TENANT_MISMATCH.value)
        lead_id = self._lead_id(payload)
        if not lead_id:
            raise ValueError(SalesLoopBlock.MISSING_LEAD_ID.value)
        event_name = str(payload.get("event_name") or payload.get("type") or "").strip()
        expected, target, default_workflow = self._route(event_name)
        current = await self._hydrate_lead(lead_id)
        self._validate_transition(current, expected, target)
        chosen_workflow = str(workflow_id or default_workflow).strip()
        if not chosen_workflow:
            raise ValueError(SalesLoopBlock.WORKFLOW_REQUIRED.value)
        payload.update({"lead_id": lead_id, "event_name": event_name, "workspace_id": self.workspace_id})

        await self._claim_event(event_id, lead_id, event_name)
        try:
            await self._claim_lead(lead_id)
            run = await self.operating_layer.handle_event(
                objective_id, chosen_workflow, event=payload, approved=approved, now=now
            )
            # Admission/review is a business result, not a provider failure. State records the trusted event transition,
            # while consequential execution remains owned by the governed engine.
            previous = current.state if current else None
            snapshot = self._next_snapshot(payload, target)
            await self._persist_state(snapshot, current)
            self._events.add(event_id)
            await self._finalize_event(event_id, "completed", {
                "accepted": bool(run.accepted), "state": target.value, "state_version": snapshot.version,
                "workflow_id": chosen_workflow, "run_id": run.run_id,
            })
            return SalesLoopResult(
                workspace_id=self.workspace_id, lead_id=lead_id, event_id=event_id,
                previous_state=previous, state=target, accepted=run.accepted,
                workflow_id=chosen_workflow, run=run,
                reason_codes=tuple(run.admission.reasons), state_version=snapshot.version,
            )
        except Exception as exc:
            try:
                await self._finalize_event(event_id, "uncertain", error=str(exc))
            finally:
                await self._release_lead(lead_id)
            raise
        finally:
            if lead_id in self._lead_claims:
                await self._release_lead(lead_id)

