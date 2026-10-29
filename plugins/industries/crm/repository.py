"""CRM repository — Supabase + in-memory fallback."""

from core.tenant_repository import require_workspace_id

import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from .models import Contact, Deal, FollowUp, Meeting, CRMActivity, CustomerOutcome, CRMMessageDelivery, CRMMessageReconciliationEvidence

logger = logging.getLogger("kanyoza.plugins.crm")


class CRMRepository:
    """Data access for CRM plugin."""

    def __init__(self, supabase_client=None):
        self.supabase = supabase_client
        self._in_memory: dict = {}

    @staticmethod
    def _production() -> bool:
        try:
            from config.settings import settings
            import os
            mode = os.getenv("SERVICE_MODE", "").strip().lower()
            return bool(getattr(settings, "is_production", False)) and mode in {
                "full", "api", "core", "director", "meta", "condition", "workers",
                "monitor", "github", "chaos",
            }
        except Exception:
            return False

    def _require_durable_write(self, operation: str) -> None:
        if self.supabase is None and self._production():
            raise RuntimeError(f"durable CRM database is required for {operation}")

    # ── Contacts ─────────────────────────────────────────────────────────

    def add_contact(self, workspace_id: str, data: dict) -> Contact:
        workspace_id = require_workspace_id(workspace_id, op="add_contact")
        self._require_durable_write("add_contact")
        contact = Contact(
            id=f"ctc-{uuid.uuid4().hex[:12]}",
            workspace_id=workspace_id,
            contact_name=data.get("contact_name", ""),
            phone=data.get("phone", ""),
            email=data.get("email"),
            company=data.get("company", ""),
            status=data.get("status", "lead"),
            source=data.get("source"),
            notes=data.get("notes"),
        )
        if self.supabase:
            try:
                self.supabase.table("crm_contacts").insert(contact.model_dump(mode="json")).execute()
            except Exception as e:
                logger.error("Supabase contact insert failed: %s", e)
                raise RuntimeError("crm contact persistence failed") from e
        self._in_memory[contact.id] = contact
        return contact

    def find_contact_by_name(self, workspace_id: str, name: str) -> Optional[Contact]:
        workspace_id = require_workspace_id(workspace_id, op="find_contact")
        name_lower = name.lower()
        if self.supabase:
            try:
                res = self.supabase.table("crm_contacts").select("*").eq("workspace_id", workspace_id).ilike("contact_name", f"%{name}%").limit(1).execute()
                if res.data:
                    return Contact(**res.data[0])
            except Exception as e:
                logger.error("Supabase contact lookup failed: %s", e)
        for c in self._in_memory.values():
            if isinstance(c, Contact) and name_lower in c.contact_name.lower():
                return c
        return None

    def find_contacts_by_status(self, workspace_id: str, status: str) -> List[Contact]:
        workspace_id = require_workspace_id(workspace_id, op="find_contacts_by_status")
        if self.supabase:
            try:
                res = self.supabase.table("crm_contacts").select("*").eq("workspace_id", workspace_id).eq("status", status).execute()
                return [Contact(**row) for row in (res.data or [])]
            except Exception as e:
                logger.error("Supabase status lookup failed: %s", e)
        return [c for c in self._in_memory.values() if isinstance(c, Contact) and c.workspace_id == workspace_id and c.status == status]

    def update_contact_status(self, workspace_id: str, contact_id: str, status: str) -> Optional[Contact]:
        workspace_id = require_workspace_id(workspace_id, op="update_contact_status")
        self._require_durable_write("update_contact_status")
        if self.supabase:
            try:
                res = self.supabase.table("crm_contacts").update({"status": status, "updated_at": datetime.now(timezone.utc).isoformat()}).eq("workspace_id", workspace_id).eq("id", contact_id).select("*").limit(1).execute()
                if res.data:
                    return Contact(**res.data[0])
            except Exception as e:
                logger.error("Supabase contact status update failed: %s", e)
                raise RuntimeError("crm contact status persistence failed") from e
        contact = self._in_memory.get(contact_id)
        if isinstance(contact, Contact) and contact.workspace_id == workspace_id:
            updated = contact.model_copy(update={"status": status, "updated_at": datetime.now(timezone.utc)})
            self._in_memory[contact_id] = updated
            return updated
        return None


    # ── Governed outbound message lifecycle ───────────────────────────────

    def create_message_delivery(self, workspace_id: str, data: dict) -> CRMMessageDelivery:
        workspace_id = require_workspace_id(workspace_id, op="create_message_delivery")
        self._require_durable_write("create_message_delivery")
        delivery = CRMMessageDelivery(
            id=f"crm-msg-{uuid.uuid4().hex[:12]}",
            workspace_id=workspace_id,
            contact_id=str(data.get("contact_id", "")),
            to_number=str(data.get("to_number", "")).strip(),
            body=str(data.get("body", "")),
            idempotency_key=str(data.get("idempotency_key", "")).strip(),
            provider=str(data.get("provider", "twilio")),
            provider_message_sid=(str(data.get("provider_message_sid")) if data.get("provider_message_sid") else None),
            provider_status=str(data.get("provider_status", "unknown")),
            status=str(data.get("status", "pending")),
            reconciliation_required=bool(data.get("reconciliation_required", True)),
            evidence=dict(data.get("evidence") or {}),
        )
        if not delivery.contact_id or not delivery.to_number or not delivery.body or not delivery.idempotency_key:
            raise ValueError("contact_id, to_number, body and idempotency_key are required")
        if self.supabase:
            try:
                self.supabase.table("crm_message_deliveries").insert(delivery.model_dump(mode="json")).execute()
            except Exception as e:
                logger.error("CRM message delivery insert failed: %s", e)
                raise RuntimeError("crm message delivery persistence failed") from e
        self._in_memory[delivery.id] = delivery
        return delivery

    def get_message_delivery(self, workspace_id: str, delivery_id: str) -> Optional[CRMMessageDelivery]:
        workspace_id = require_workspace_id(workspace_id, op="get_message_delivery")
        obj = self._in_memory.get(delivery_id)
        if isinstance(obj, CRMMessageDelivery) and obj.workspace_id == workspace_id:
            return obj
        if self.supabase:
            try:
                res = self.supabase.table("crm_message_deliveries").select("*").eq("workspace_id", workspace_id).eq("id", delivery_id).limit(1).execute()
                if res.data:
                    return CRMMessageDelivery(**res.data[0])
            except Exception as e:
                logger.warning("CRM message delivery lookup failed: %s", e)
        return None

    def get_message_delivery_by_idempotency_key(self, workspace_id: str, idempotency_key: str) -> Optional[CRMMessageDelivery]:
        workspace_id = require_workspace_id(workspace_id, op="get_message_delivery_by_idempotency_key")
        if self.supabase:
            try:
                res = self.supabase.table("crm_message_deliveries").select("*").eq("workspace_id", workspace_id).eq("idempotency_key", idempotency_key).limit(1).execute()
                if res.data:
                    row = CRMMessageDelivery(**res.data[0])
                    self._in_memory[row.id] = row
                    return row
            except Exception as e:
                logger.warning("CRM message idempotency lookup failed: %s", e)
        return next((x for x in self._in_memory.values() if isinstance(x, CRMMessageDelivery) and x.workspace_id == workspace_id and x.idempotency_key == idempotency_key), None)

    def update_message_delivery(self, workspace_id: str, delivery_id: str, **changes) -> CRMMessageDelivery:
        delivery = self.get_message_delivery(workspace_id, delivery_id)
        if delivery is None:
            raise ValueError("message delivery not found in workspace")
        self._require_durable_write("update_message_delivery")
        updated = delivery.model_copy(update={**changes, "updated_at": datetime.now(timezone.utc)})
        if self.supabase:
            try:
                self.supabase.table("crm_message_deliveries").update(updated.model_dump(mode="json")).eq("workspace_id", workspace_id).eq("id", delivery_id).execute()
            except Exception as e:
                logger.error("CRM message delivery update failed: %s", e)
                raise RuntimeError("crm message delivery persistence failed") from e
        self._in_memory[delivery_id] = updated
        return updated

    def list_reconciliation_required_message_deliveries(self, workspace_id: str | None = None, limit: int = 100):
        if workspace_id is not None:
            workspace_id = require_workspace_id(workspace_id, op="list_reconciliation_required_message_deliveries")
        if self.supabase:
            try:
                query = self.supabase.table("crm_message_deliveries").select("*").eq("reconciliation_required", True).order("created_at").limit(limit)
                if workspace_id is not None:
                    query = query.eq("workspace_id", workspace_id)
                result = query.execute()
                return [CRMMessageDelivery(**row) for row in (result.data or [])]
            except Exception as e:
                logger.warning("CRM pending message lookup failed: %s", e)
        rows = [x for x in self._in_memory.values() if isinstance(x, CRMMessageDelivery) and x.reconciliation_required and (workspace_id is None or x.workspace_id == workspace_id)]
        return sorted(rows, key=lambda x: x.created_at)[:limit]

    def record_message_reconciliation_evidence(self, workspace_id: str, data: dict) -> CRMMessageReconciliationEvidence:
        workspace_id = require_workspace_id(workspace_id, op="record_message_reconciliation_evidence")
        self._require_durable_write("record_message_reconciliation_evidence")
        delivery_id = str(data.get("delivery_id", ""))
        if self.get_message_delivery(workspace_id, delivery_id) is None:
            raise ValueError("message delivery not found in workspace")
        row = CRMMessageReconciliationEvidence(
            id=f"crm-msg-rec-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, delivery_id=delivery_id,
            provider=str(data.get("provider", "twilio")), provider_reference=str(data.get("provider_reference", "")),
            attempt_number=int(data.get("attempt_number", 0)), provider_status=str(data.get("provider_status", "unknown")),
            canonical_status=str(data.get("canonical_status", "uncertain")), terminal=bool(data.get("terminal", False)),
            verification_state=str(data.get("verification_state", "provider_observed_nonterminal")),
            observed=dict(data.get("observed") or {}), evidence=dict(data.get("evidence") or {}),
        )
        if self.supabase:
            try:
                self.supabase.table("crm_message_reconciliation_evidence").insert(row.model_dump(mode="json")).execute()
            except Exception as e:
                logger.error("CRM message reconciliation evidence insert failed: %s", e)
                raise RuntimeError("crm message reconciliation evidence persistence failed") from e
        self._in_memory[row.id] = row
        return row

    def list_message_reconciliation_evidence(self, workspace_id: str, delivery_id: str, limit: int = 100):
        workspace_id = require_workspace_id(workspace_id, op="list_message_reconciliation_evidence")
        if self.supabase:
            try:
                result = self.supabase.table("crm_message_reconciliation_evidence").select("*").eq("workspace_id", workspace_id).eq("delivery_id", delivery_id).order("attempt_number", desc=True).limit(limit).execute()
                return [CRMMessageReconciliationEvidence(**row) for row in (result.data or [])]
            except Exception as e:
                logger.warning("CRM message evidence lookup failed: %s", e)
        rows = [x for x in self._in_memory.values() if isinstance(x, CRMMessageReconciliationEvidence) and x.workspace_id == workspace_id and x.delivery_id == delivery_id]
        return sorted(rows, key=lambda x: x.attempt_number, reverse=True)[:limit]

    # ── Deals ────────────────────────────────────────────────────────────

    def add_deal(self, workspace_id: str, data: dict) -> Deal:
        workspace_id = require_workspace_id(workspace_id, op="add_deal")
        self._require_durable_write("add_deal")
        deal = Deal(
            id=f"deal-{uuid.uuid4().hex[:12]}",
            workspace_id=workspace_id,
            contact_id=data.get("contact_id", ""),
            contact_name=data.get("contact_name", ""),
            amount=float(data.get("amount", 0)),
            stage=data.get("stage", "lead"),
            deal_date=data.get("deal_date", datetime.now(timezone.utc).strftime("%Y-%m-%d")),
            probability=data.get("probability", 20),
        )
        if self.supabase:
            try:
                self.supabase.table("crm_deals").insert(deal.model_dump(mode="json")).execute()
            except Exception as e:
                logger.error("Supabase deal insert failed: %s", e)
                raise RuntimeError("crm deal persistence failed") from e
        self._in_memory[deal.id] = deal
        return deal

    def update_deal_stage(self, workspace_id: str, deal_id: str, stage: str) -> Optional[Deal]:
        workspace_id = require_workspace_id(workspace_id, op="update_deal_stage")
        self._require_durable_write("update_deal_stage")
        if self.supabase:
            try:
                res = self.supabase.table("crm_deals").update({"stage": stage}).eq("workspace_id", workspace_id).eq("id", deal_id).select("*").limit(1).execute()
                if res.data:
                    return Deal(**res.data[0])
            except Exception as e:
                logger.error("Supabase deal stage update failed: %s", e)
                raise RuntimeError("crm deal stage persistence failed") from e
        deal = self._in_memory.get(deal_id)
        if isinstance(deal, Deal) and deal.workspace_id == workspace_id:
            updated = deal.model_copy(update={"stage": stage})
            self._in_memory[deal_id] = updated
            return updated
        return None

    # ── Follow-ups ───────────────────────────────────────────────────────

    def add_followup(self, workspace_id: str, data: dict) -> FollowUp:
        workspace_id = require_workspace_id(workspace_id, op="add_followup")
        self._require_durable_write("add_followup")
        followup = FollowUp(
            id=f"flw-{uuid.uuid4().hex[:12]}",
            workspace_id=workspace_id,
            contact_id=data.get("contact_id", ""),
            contact_name=data.get("contact_name", ""),
            followup_date=data.get("followup_date", ""),
            notes=data.get("notes", ""),
            status=data.get("status", "scheduled"),
        )
        if self.supabase:
            try:
                self.supabase.table("crm_followups").insert(followup.model_dump(mode="json")).execute()
            except Exception as e:
                logger.error("Supabase followup insert failed: %s", e)
                raise RuntimeError("crm follow-up persistence failed") from e
        self._in_memory[followup.id] = followup
        return followup

    # ── Meetings ─────────────────────────────────────────────────────────

    def add_meeting(self, workspace_id: str, data: dict) -> Meeting:
        workspace_id = require_workspace_id(workspace_id, op="add_meeting")
        self._require_durable_write("add_meeting")
        meeting = Meeting(
            id=f"mtg-{uuid.uuid4().hex[:12]}",
            workspace_id=workspace_id,
            title=data.get("title", ""),
            description=data.get("description"),
            meeting_date=data.get("meeting_date", ""),
            start_time=data.get("start_time"),
            location=data.get("location", "Virtual"),
        )
        if self.supabase:
            try:
                self.supabase.table("crm_meetings").insert(meeting.model_dump(mode="json")).execute()
            except Exception as e:
                logger.error("Supabase meeting insert failed: %s", e)
                raise RuntimeError("crm meeting persistence failed") from e
        self._in_memory[meeting.id] = meeting
        return meeting


    # ── Completion loop ─────────────────────────────────────────────────

    def add_activity(self, workspace_id: str, data: dict) -> CRMActivity:
        workspace_id = require_workspace_id(workspace_id, op="add_activity")
        self._require_durable_write("add_activity")
        activity = CRMActivity(
            id=f"act-{uuid.uuid4().hex[:12]}",
            workspace_id=workspace_id,
            contact_id=data.get("contact_id"),
            deal_id=data.get("deal_id"),
            activity_type=str(data.get("activity_type", "note")).strip().lower(),
            status=str(data.get("status", "completed")).strip().lower(),
            subject=str(data.get("subject", "")).strip(),
            notes=str(data.get("notes", "")),
            occurred_at=data.get("occurred_at") or datetime.now(timezone.utc),
        )
        if not activity.activity_type:
            raise ValueError("activity_type is required")
        if self.supabase:
            try:
                self.supabase.table("crm_activities").insert(activity.model_dump(mode="json")).execute()
            except Exception as e:
                logger.error("Supabase CRM activity insert failed: %s", e)
                raise RuntimeError("crm activity persistence failed") from e
        self._in_memory[activity.id] = activity
        return activity

    def record_outcome(self, workspace_id: str, data: dict) -> CustomerOutcome:
        workspace_id = require_workspace_id(workspace_id, op="record_outcome")
        self._require_durable_write("record_outcome")
        outcome = CustomerOutcome(
            id=f"out-{uuid.uuid4().hex[:12]}",
            workspace_id=workspace_id,
            contact_id=data.get("contact_id"),
            deal_id=data.get("deal_id"),
            metric=str(data.get("metric", "")).strip(),
            value=float(data.get("value", 0)),
            source=str(data.get("source", "crm")).strip() or "crm",
            evidence=dict(data.get("evidence") or {}),
        )
        if not outcome.metric:
            raise ValueError("metric is required")
        if self.supabase:
            try:
                self.supabase.table("crm_outcomes").insert(outcome.model_dump(mode="json")).execute()
            except Exception as e:
                logger.error("Supabase CRM outcome insert failed: %s", e)
                raise RuntimeError("crm outcome persistence failed") from e
        self._in_memory[outcome.id] = outcome
        return outcome
