"""Governed Action Execution Kernel.

All consequential business actions enter here as typed ActionIntent objects.
The kernel performs contract validation, workspace scoping, approval/risk checks,
durable intent recording, and execution through the existing ExecutionLedger /
ExternalActionExecutor boundary.

This is deliberately provider-agnostic. Publishing remains compatible with its
canonical pipeline while business actions can now share the same governance
primitive without copying connector-specific logic.

Receipt contract (v2):
  - publish_content receives the container from parameters so the canonical
    pipeline can resolve a live connector. Without it, plugin resolution
    silently fails and the pipeline fabricates success.
  - publish_content executes the canonical pipeline inline (durable=False) so
    the Action Kernel receives the real receipt, not a queue acknowledgement.
    The outbox remains available for scheduled/manual callers who pass
    durable=True from a different entry point.
  - Runtime references (like the service container) are stripped from the
    parameters before deepcopy / persistence so the kernel can hash the intent
    and store it in action_outbox without attempting to serialize thread locks
    or HTTP clients. They are reattached for handler execution only.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from services.execution_ledger import ExecutionLedger
from services.external_action_executor import ExternalActionExecutor

logger = logging.getLogger("kanyoza.action_kernel")

RISKS = {"low", "medium", "high", "critical"}


@dataclass(frozen=True)
class ActionIntent:
    workspace_id: str
    action_type: str
    parameters: dict[str, Any] = field(default_factory=dict)
    risk_level: str = "low"
    decision_id: Optional[str] = None
    correlation_id: Optional[str] = None
    idempotency_key: str = ""
    source: str = "unknown"

    def validate(self) -> None:
        if not self.workspace_id.strip():
            raise ValueError("ActionIntent.workspace_id is required")
        if not self.action_type.strip():
            raise ValueError("ActionIntent.action_type is required")
        if self.risk_level not in RISKS:
            raise ValueError(f"invalid risk_level: {self.risk_level}")
        if not self.idempotency_key.strip():
            raise ValueError("ActionIntent.idempotency_key is required")


@dataclass(frozen=True)
class ActionContract:
    action_type: str
    risk_level: str = "low"
    required_parameters: tuple[str, ...] = ()
    irreversible: bool = False
    requires_approval: bool = False
    handler: Optional[Callable[..., Any]] = None
    reconciler: Optional[Callable[..., Any]] = None
    autonomous_retryable: bool = False

    def validate(self, intent: ActionIntent) -> None:
        if intent.action_type != self.action_type:
            raise ValueError("action contract mismatch")
        if intent.risk_level != self.risk_level:
            raise ValueError(
                f"risk mismatch for {self.action_type}: intent={intent.risk_level} contract={self.risk_level}"
            )
        missing = [k for k in self.required_parameters if intent.parameters.get(k) in (None, "")]
        if missing:
            raise ValueError(f"{self.action_type} missing required parameters: {', '.join(missing)}")


@dataclass(frozen=True)
class ActionResult:
    status: str
    action_type: str
    execution_id: str
    intent_id: str
    output: dict[str, Any] = field(default_factory=dict)
    reconciliation_required: bool = False
    replayed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.status == "succeeded",
            "status": self.status,
            "action_type": self.action_type,
            "execution_id": self.execution_id,
            "intent_id": self.intent_id,
            "output": self.output,
            "reconciliation_required": self.reconciliation_required,
            "replayed": self.replayed,
        }


class ActionKernel:
    """Single governed execution boundary for typed business actions."""

    def __init__(self, db=None, *, allow_in_memory: bool = False):
        self.db = db
        self.allow_in_memory = allow_in_memory
        self.ledger = ExecutionLedger(db=db, allow_in_memory=allow_in_memory)
        self.executor = ExternalActionExecutor(self.ledger)
        self._contracts: dict[str, ActionContract] = {}
        self._memory_intents: dict[str, dict[str, Any]] = {}

    def register(self, contract: ActionContract) -> None:
        if contract.action_type in self._contracts:
            raise ValueError(f"action already registered: {contract.action_type}")
        if contract.risk_level not in RISKS:
            raise ValueError("invalid contract risk")
        if not callable(contract.handler):
            raise ValueError("action contract requires a callable handler")
        self._contracts[contract.action_type] = contract

    # ── Parameter hygiene ────────────────────────────────────────────

    @staticmethod
    def _split_params(parameters: Optional[dict[str, Any]]) -> tuple[dict[str, Any], Any]:
        """Separate JSON-serializable parameters from non-serializable refs.

        The Action Kernel attaches runtime references (like the service
        container) to parameters so handlers can reach live services. Those
        references are not deep-copyable or JSON-serializable. This helper
        pulls them out so the kernel can deepcopy / persist the rest, then
        reattach them for handler execution.

        Returns:
            (safe_params, container_ref):
                safe_params    — the deepcopy-able portion (JSON-safe values)
                container_ref  — the runtime reference or None
        """
        raw = dict(parameters or {})
        container_ref = raw.pop("container", None)
        return raw, container_ref

    @staticmethod
    def _reattach(params: dict[str, Any], container_ref: Any) -> dict[str, Any]:
        """Reattach the runtime container to handler-facing parameters."""
        if container_ref is not None:
            params["container"] = container_ref
        return params

    @staticmethod
    def make_idempotency_key(workspace_id: str, action_type: str, parameters: dict[str, Any], supplied: str = "") -> str:
        if supplied:
            return supplied
        canonical = json.dumps(parameters or {}, sort_keys=True, separators=(",", ":"), default=str)
        return ExecutionLedger.make_idempotency_key(workspace_id, action_type, canonical)

    # ── Durable intent persistence ────────────────────────────────────

    def _persist_intent(self, intent_id: str, intent: ActionIntent, *, status: str = "pending") -> None:
        # Strip runtime references (container and friends) — the durable
        # action_outbox row must contain only JSON-safe data. Handlers receive
        # the container via the in-memory intent, not by reading it back from
        # the database.
        safe_params, _ = self._split_params(intent.parameters)
        row = {
            "intent_id": intent_id,
            "workspace_id": intent.workspace_id,
            "action_type": intent.action_type,
            "parameters": copy.deepcopy(safe_params),
            "risk_level": intent.risk_level,
            "decision_id": intent.decision_id,
            "correlation_id": intent.correlation_id,
            "idempotency_key": intent.idempotency_key,
            "source": intent.source,
            "status": status,
        }
        if self.db is None:
            self._memory_intents[intent_id] = row
            return
        try:
            self.db.table("action_outbox").upsert(row, on_conflict="workspace_id,idempotency_key").execute()
        except TypeError:
            self.db.table("action_outbox").upsert(row).execute()
        except Exception:
            # A durable intent is part of the safety boundary. Never execute a
            # side effect if its authoritative outbox record could not be stored.
            raise RuntimeError("durable action intent persistence failed")

    def _update_intent(self, intent_id: str, workspace_id: str, **fields: Any) -> None:
        if self.db is None:
            row = self._memory_intents.get(intent_id)
            if row:
                row.update(fields)
            return
        try:
            self.db.table("action_outbox").update(fields).eq("intent_id", intent_id).eq("workspace_id", workspace_id).execute()
        except Exception:
            logger.exception("action outbox finalization failed intent=%s", intent_id)
            raise RuntimeError("durable action intent finalization failed")

    def _build_intent(
        self, *, workspace_id: str, action_type: str, parameters: Optional[dict[str, Any]],
        risk_level: str, decision_id: Optional[str], correlation_id: Optional[str],
        idempotency_key: Optional[str], source: str,
    ) -> tuple[ActionIntent, ActionContract, str]:
        # Strip runtime refs before deepcopy, then reattach them so the handler
        # can access the container during execution.
        safe_params, container_ref = self._split_params(parameters)
        params = copy.deepcopy(safe_params)
        self._reattach(params, container_ref)

        key = self.make_idempotency_key(workspace_id, action_type, params, idempotency_key or "")
        intent = ActionIntent(
            workspace_id=str(workspace_id or "").strip(), action_type=str(action_type or "").strip(),
            parameters=params, risk_level=str(risk_level or "low").lower(), decision_id=decision_id,
            correlation_id=correlation_id or uuid.uuid4().hex, idempotency_key=key, source=source,
        )
        intent.validate()
        contract = self._contracts.get(intent.action_type)
        if contract is None:
            raise ValueError(f"unsupported governed action: {intent.action_type}")
        contract.validate(intent)
        intent_id = "act_" + hashlib.sha256(f"{intent.workspace_id}|{intent.idempotency_key}".encode()).hexdigest()[:24]
        return intent, contract, intent_id

    # ── Enqueue / approve (durable, async callers) ────────────────────

    async def enqueue(
        self, *, workspace_id: str, action_type: str, parameters: Optional[dict[str, Any]] = None,
        risk_level: str = "low", decision_id: Optional[str] = None, correlation_id: Optional[str] = None,
        idempotency_key: Optional[str] = None, source: str = "unknown", approved: bool = False,
    ) -> dict[str, Any]:
        """Durably accept an action without performing its external side effect.

        This is the preferred boundary for asynchronous callers. The caller receives
        acceptance only after the authoritative action_outbox row exists.
        """
        # Strip runtime refs before persistence — they don't belong in the DB.
        safe_params, _container_ref = self._split_params(parameters)
        intent, contract, intent_id = self._build_intent(
            workspace_id=workspace_id, action_type=action_type, parameters=safe_params,
            risk_level=risk_level, decision_id=decision_id, correlation_id=correlation_id,
            idempotency_key=idempotency_key, source=source,
        )
        governed = contract.requires_approval or contract.irreversible or intent.risk_level in {"high", "critical"}
        if governed and not approved:
            status = "approval_required"
        else:
            status = "pending"
        if self.db is None:
            existing = self._memory_intents.get(intent_id)
            if existing:
                return {"ok": True, "status": existing.get("status", "pending"), "intent_id": intent_id,
                        "action_type": intent.action_type, "idempotency_key": intent.idempotency_key,
                        "accepted": existing.get("status") == "pending"}
            self._persist_intent(intent_id, intent, status=status)
            self._memory_intents[intent_id]["approved"] = bool(approved)
        else:
            row = {
                "intent_id": intent_id, "workspace_id": intent.workspace_id, "action_type": intent.action_type,
                "parameters": copy.deepcopy(safe_params), "risk_level": intent.risk_level,
                "decision_id": intent.decision_id, "correlation_id": intent.correlation_id,
                "idempotency_key": intent.idempotency_key, "source": intent.source, "status": status,
                "approved": bool(approved),
            }
            try:
                inserted = await asyncio.to_thread(lambda: self.db.table("action_outbox").insert(row).execute())
                if not getattr(inserted, "data", None):
                    raise RuntimeError("durable action intent insert returned no authoritative row")
            except Exception as exc:
                # A uniqueness conflict is normal replay; recover the authoritative
                # existing row rather than resetting its terminal state. Other DB
                # failures remain fail-closed.
                try:
                    existing = await asyncio.to_thread(lambda: self.db.table("action_outbox").select("*").eq("workspace_id", intent.workspace_id).eq("idempotency_key", intent.idempotency_key).maybe_single().execute())
                    existing_row = getattr(existing, "data", None)
                    if not existing_row:
                        raise RuntimeError("durable action intent persistence failed") from exc
                    return {"ok": True, "status": existing_row.get("status", "pending"), "intent_id": existing_row.get("intent_id", intent_id),
                            "action_type": intent.action_type, "idempotency_key": intent.idempotency_key,
                            "accepted": existing_row.get("status") == "pending"}
                except Exception:
                    raise RuntimeError("durable action intent persistence failed") from exc
        return {
            "ok": True, "status": status, "intent_id": intent_id,
            "action_type": intent.action_type, "idempotency_key": intent.idempotency_key,
            "accepted": status == "pending",
        }

    async def approve(self, *, workspace_id: str, intent_id: str) -> dict[str, Any]:
        """Approve a previously accepted high-impact intent without executing it inline."""
        ws = str(workspace_id or "").strip()
        iid = str(intent_id or "").strip()
        if not ws or not iid:
            raise ValueError("workspace_id and intent_id are required")
        if self.db is None:
            row = self._memory_intents.get(iid)
            if not row or row.get("workspace_id") != ws:
                raise ValueError("action intent not found")
            row["approved"] = True
            row["status"] = "pending"
            return {"ok": True, "status": "pending", "intent_id": iid}
        from datetime import datetime, timezone
        result = await asyncio.to_thread(lambda: self.db.table("action_outbox").update({
            "approved": True, "status": "pending", "updated_at": datetime.now(timezone.utc).isoformat()
        }).eq("intent_id", iid).eq("workspace_id", ws).eq("status", "approval_required").execute())
        if not getattr(result, "data", None):
            raise ValueError("action intent not found or not awaiting approval")
        return {"ok": True, "status": "pending", "intent_id": iid}

    # ── Outbox worker entry point ─────────────────────────────────────

    async def process_outbox_row(self, row: dict[str, Any]) -> dict[str, Any]:
        """Execute one worker-claimed durable intent through the same kernel boundary."""
        intent, contract, intent_id = self._build_intent(
            workspace_id=row.get("workspace_id"), action_type=row.get("action_type"),
            parameters=row.get("parameters") or {}, risk_level=row.get("risk_level") or "low",
            decision_id=row.get("decision_id"), correlation_id=row.get("correlation_id"),
            idempotency_key=row.get("idempotency_key"), source=row.get("source") or "action_outbox_worker",
        )
        approved = bool(row.get("approved", False))
        if contract.requires_approval or contract.irreversible or intent.risk_level in {"high", "critical"}:
            if not approved:
                self._update_intent(intent_id, intent.workspace_id, status="approval_required")
                return ActionResult("approval_required", intent.action_type, str(row.get("execution_id") or ""), intent_id).to_dict()

        self._update_intent(intent_id, intent.workspace_id, status="processing")
        operation = lambda: contract.handler(intent.parameters, intent)
        result = await self.executor.execute(
            workspace_id=intent.workspace_id, action=f"action.{intent.action_type}",
            idempotency_key=intent.idempotency_key,
            provider=f"action:{intent.action_type.split('_', 1)[0]}", operation=operation,
            metadata={"intent_id": intent_id, "decision_id": intent.decision_id,
                      "correlation_id": intent.correlation_id, "source": intent.source},
        )
        status = str(result.get("status") or "uncertain")
        output = result.get("output") if isinstance(result.get("output"), dict) else {}
        if not output and isinstance(result.get("result"), dict):
            output = copy.deepcopy(result["result"])
        if result.get("error") and "error" not in output:
            output["error"] = result["error"]
        # Another worker/process may already own the durable Execution Ledger
        # lease for this idempotency key. That is a live ownership signal, not a
        # terminal outbox result. Never pass it to finalize_action_outbox: the DB
        # finalizer intentionally rejects non-terminal statuses. Leaving the row
        # processing under its current lease preserves fencing; once the owner
        # completes, the execution ledger becomes authoritative on replay.
        if status == "in_progress":
            return ActionResult(
                status="in_progress", action_type=intent.action_type,
                execution_id=str(result.get("execution_id") or ""), intent_id=intent_id,
                output=output, reconciliation_required=False,
                replayed=bool(result.get("replayed")),
            ).to_dict()
        reconciliation = status == "uncertain" or bool(result.get("reconciliation_required"))
        final_status = "reconciliation_required" if reconciliation else status
        if self.db is not None and row.get("lease_token"):
            finalized = await asyncio.to_thread(lambda: self.db.rpc("finalize_action_outbox", {
                "p_intent_id": intent_id, "p_workspace_id": intent.workspace_id,
                "p_lease_token": row.get("lease_token"), "p_status": final_status,
                "p_execution_id": result.get("execution_id"),
                "p_provider_object_id": result.get("provider_object_id"),
                "p_output": output, "p_error": result.get("error"),
            }).execute())
            if not getattr(finalized, "data", False):
                return ActionResult("uncertain", intent.action_type, str(result.get("execution_id") or ""), intent_id, output, True, bool(result.get("replayed"))).to_dict()
        else:
            self._update_intent(intent_id, intent.workspace_id, status=final_status,
                execution_id=result.get("execution_id"), provider_object_id=result.get("provider_object_id"),
                output=output, error=result.get("error"))
        return ActionResult(
            status=status, action_type=intent.action_type,
            execution_id=str(result.get("execution_id") or ""), intent_id=intent_id,
            output=output, reconciliation_required=reconciliation,
            replayed=bool(result.get("replayed")),
        ).to_dict()

    # ── Inline execution (synchronous callers) ────────────────────────

    async def execute(
        self,
        *,
        workspace_id: str,
        action_type: str,
        parameters: Optional[dict[str, Any]] = None,
        risk_level: str = "low",
        decision_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        source: str = "unknown",
        approved: bool = False,
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        # Strip non-serializable refs before deepcopy, then reattach them for
        # the handler. Without this, a live container (with thread locks and
        # httpx sessions) breaks copy.deepcopy().
        safe_params, container_ref = self._split_params(parameters)
        params = copy.deepcopy(safe_params)
        self._reattach(params, container_ref)

        key = self.make_idempotency_key(workspace_id, action_type, params, idempotency_key or "")
        intent = ActionIntent(
            workspace_id=str(workspace_id or "").strip(),
            action_type=str(action_type or "").strip(),
            parameters=params,
            risk_level=str(risk_level or "low").lower(),
            decision_id=decision_id,
            correlation_id=correlation_id or uuid.uuid4().hex,
            idempotency_key=key,
            source=source,
        )
        intent.validate()
        contract = self._contracts.get(intent.action_type)
        if contract is None:
            raise ValueError(f"unsupported governed action: {intent.action_type}")
        contract.validate(intent)
        if contract.requires_approval or contract.irreversible or intent.risk_level in {"high", "critical"}:
            if not approved:
                return ActionResult(
                    status="approval_required", action_type=intent.action_type,
                    execution_id="", intent_id="", output={"reason": "governed action requires approval"}
                ).to_dict()

        intent_id = "act_" + hashlib.sha256(f"{intent.workspace_id}|{intent.idempotency_key}".encode()).hexdigest()[:24]
        self._persist_intent(intent_id, intent, status="pending")
        self._update_intent(intent_id, intent.workspace_id, status="processing")

        operation = lambda: contract.handler(intent.parameters, intent)
        try:
            result = await self.executor.execute(
                workspace_id=intent.workspace_id,
                action=f"action.{intent.action_type}",
                idempotency_key=intent.idempotency_key,
                provider=f"action:{intent.action_type.split('_', 1)[0]}",
                operation=operation,
                metadata={
                    "intent_id": intent_id,
                    "decision_id": intent.decision_id,
                    "correlation_id": intent.correlation_id,
                    "source": intent.source,
                    **(metadata or {}),
                },
            )
        except Exception as exc:
            self._update_intent(intent_id, intent.workspace_id, status="failed", error=str(exc)[:1000])
            raise

        status = str(result.get("status") or "uncertain")
        output = result.get("output") if isinstance(result.get("output"), dict) else {}
        if not output and isinstance(result.get("result"), dict):
            output = copy.deepcopy(result.get("result"))
        if not output:
            output = {k: v for k, v in result.items() if k not in {"status", "execution_id", "provider_object_id", "replayed"}}
        if result.get("error") and "error" not in output:
            output["error"] = result.get("error")
        reconciliation = status == "uncertain" or bool(result.get("reconciliation_required"))
        final_status = "reconciliation_required" if reconciliation else status
        try:
            self._update_intent(
                intent_id, intent.workspace_id,
                status=final_status,
                execution_id=result.get("execution_id"),
                provider_object_id=result.get("provider_object_id"),
                output=output,
                error=result.get("error"),
            )
        except Exception:
            # ExternalActionExecutor already converts receipt persistence failure
            # into uncertainty. If the intent projection itself fails, retain the
            # conservative execution result and never turn it into success.
            if status == "succeeded":
                return ActionResult(
                    status="uncertain", action_type=intent.action_type,
                    execution_id=str(result.get("execution_id") or ""), intent_id=intent_id,
                    output=output, reconciliation_required=True,
                    replayed=bool(result.get("replayed")),
                ).to_dict()
            raise

        return ActionResult(
            status=status,
            action_type=intent.action_type,
            execution_id=str(result.get("execution_id") or ""),
            intent_id=intent_id,
            output=output,
            reconciliation_required=reconciliation,
            replayed=bool(result.get("replayed")),
        ).to_dict()


def build_default_action_kernel(db=None, *, allow_in_memory: bool = False, plugin=None) -> ActionKernel:
    """Register the first governed business-action family (CRM)."""
    from plugins.industries.crm.plugin import CRMPlugin

    plugin = plugin or CRMPlugin(db)

    async def run_crm(command: str, parameters: dict[str, Any], intent: ActionIntent) -> dict[str, Any]:
        payload = dict(parameters)
        payload.pop("workspace_id", None)
        value = plugin.execute(command, payload, workspace_id=intent.workspace_id)
        if inspect.isawaitable(value):
            value = await value
        if not isinstance(value, dict):
            raise RuntimeError(f"CRM action {command} returned a non-object result")
        if value.get("ok") is False:
            # A deterministic provider/domain rejection is a failed action, not
            # an ambiguous transport outcome.
            return {"ok": False, "outcome": "failed", "error": value.get("error") or "CRM action failed"}

        # Promote the CRM resource ID to the top level so the executor's v2
        # receipt contract (ExternalActionExecutor._classify_status) can
        # classify this as `succeeded` rather than `uncertain`. The plugin
        # nests the ID under `data` (e.g. data.contact_id), but the executor
        # only scans the top level for _PROVIDER_OBJECT_ID_KEYS. Without a
        # top-level object ID, an `ok=True` result is treated as fabricated
        # success and downgraded to `uncertain`, forcing reconciliation.
        data = value.get("data") if isinstance(value.get("data"), dict) else {}
        object_id = (
            data.get("contact_id")
            or data.get("deal_id")
            or data.get("followup_id")
            or data.get("meeting_id")
        )
        promoted = {"ok": True, **value}
        if object_id:
            promoted["object_id"] = object_id
        return promoted

    kernel = ActionKernel(db, allow_in_memory=allow_in_memory)
    kernel.register(ActionContract(
        "crm_add_contact", risk_level="medium", required_parameters=("name",),
        requires_approval=False, autonomous_retryable=True, handler=lambda p, i: run_crm("add_contact", {
            "name": p.get("name"), "phone": p.get("phone", ""), "email": p.get("email"),
            "company": p.get("company", ""), "status": p.get("status", "lead")}, i)))
    kernel.register(ActionContract(
        "crm_add_deal", risk_level="high", required_parameters=("amount", "contact"),
        requires_approval=True, handler=lambda p, i: run_crm("add_deal", {
            "amount": p.get("amount"), "contact": p.get("contact"),
            "stage": p.get("stage", "lead"), "date": p.get("date")}, i)))
    kernel.register(ActionContract(
        "crm_update_contact_status", risk_level="medium", required_parameters=("contact_id", "status"),
        autonomous_retryable=True, handler=lambda p, i: run_crm("update_contact_status", {"contact_id": p.get("contact_id"), "status": p.get("status")}, i)))
    kernel.register(ActionContract(
        "crm_update_deal_stage", risk_level="medium", required_parameters=("deal_id", "stage"),
        autonomous_retryable=True, handler=lambda p, i: run_crm("update_deal_stage", {"deal_id": p.get("deal_id"), "stage": p.get("stage")}, i)))
    kernel.register(ActionContract(
        "crm_schedule_followup", risk_level="medium", required_parameters=("contact", "date"),
        autonomous_retryable=True, handler=lambda p, i: run_crm("schedule_followup", {
            "contact": p.get("contact"), "date": p.get("date"), "notes": p.get("notes", "")}, i)))
    kernel.register(ActionContract(
        "crm_add_meeting", risk_level="medium", required_parameters=("title",),
        autonomous_retryable=True, handler=lambda p, i: run_crm("add_meeting", {
            "title": p.get("title"), "description": p.get("description"), "date": p.get("date", ""),
            "time": p.get("time"), "location": p.get("location", "Virtual")}, i)))

    # Customer communication: outbound WhatsApp is a governed action, not a
    # direct connector call from automation. The account is resolved by exact
    # social-account ID and workspace, preventing cross-tenant account use.
    async def whatsapp_message(p, i):
        account_id = str(p.get("social_account_id") or "").strip()
        recipient = str(p.get("to_number") or p.get("recipient") or "").strip()
        message = str(p.get("message") or "").strip()
        if not account_id or not recipient or not message:
            return {"ok": False, "outcome": "failed", "error": "whatsapp_message requires social_account_id, recipient, and message"}
        from repositories.social_account_repository import SocialAccountRepository
        from services.connectors.whatsapp_connector import WhatsAppConnector
        repo = SocialAccountRepository(db)
        account = repo.find_by_id(account_id)
        if account is None or str(account.workspace_id) != i.workspace_id:
            return {"ok": False, "outcome": "failed", "error": "WhatsApp account not found in workspace"}
        if not account.enabled:
            return {"ok": False, "outcome": "failed", "error": "WhatsApp account is disabled"}
        if str(account.platform).lower() != "whatsapp":
            return {"ok": False, "outcome": "failed", "error": "selected social account is not WhatsApp"}
        connector = WhatsAppConnector(account)
        result = await asyncio.to_thread(connector.publish, message, to_number=recipient)
        if not getattr(result, "ok", False):
            outcome = getattr(result, "outcome", "failed") or "failed"
            return {"ok": False, "outcome": outcome, "error": getattr(result, "error", None) or "WhatsApp send failed"}
        return {"ok": True, "outcome": "succeeded", "provider_message_id": getattr(result, "post_id", ""), "recipient": recipient, "platform": "whatsapp"}

    kernel.register(ActionContract(
        "whatsapp_message", risk_level="high",
        required_parameters=("social_account_id", "to_number", "message"),
        requires_approval=True, handler=whatsapp_message))

    async def facebook_message(p, i):
        recipient = str(p.get("recipient_id") or "").strip()
        message = str(p.get("message") or "").strip()
        if not recipient or not message:
            return {"ok": False, "outcome": "failed", "error": "facebook_message requires recipient_id and message"}
        from services.facebook import facebook
        sent = await asyncio.to_thread(facebook.send_message, recipient, message)
        if sent:
            return {"ok": True, "outcome": "succeeded", "recipient_id": recipient}
        return {"ok": False, "outcome": "failed", "error": "Facebook message transport rejected the send"}

    kernel.register(ActionContract(
        "facebook_message", risk_level="medium",
        required_parameters=("recipient_id", "message"),
        handler=facebook_message))

    # Internal operator notification is a governed side effect too.  The
    # notifier provider is intentionally reached only from this Action Kernel
    # contract, so Business Automation cannot create a parallel notification
    # execution plane.
    async def notify_operator(p, i):
        message = str(p.get("message") or "").strip()
        if not message:
            return {"ok": False, "outcome": "failed", "error": "notify_operator requires message"}
        level = str(p.get("level") or "info").lower()
        if level not in {"info", "warning", "error", "critical"}:
            return {"ok": False, "outcome": "failed", "error": "invalid notification level"}
        channels = p.get("channels") or ["slack"]
        allowed = {"slack", "discord", "email"}
        if not isinstance(channels, list) or not set(channels).issubset(allowed):
            return {"ok": False, "outcome": "failed", "error": "channels must be a subset of slack, discord, email"}
        from services.notifier import notifier
        def deliver():
            results = notifier.alert(message, level=level, channels=channels)
            delivered = [name for name in channels if bool(results.get(name))]
            failed = [name for name in channels if not bool(results.get(name))]
            if delivered and failed:
                return {"ok": False, "outcome": "partial", "delivery_status": "partial", "delivered": delivered, "failed": failed, "level": level, "reconciliation_required": True}
            if not delivered:
                return {"ok": False, "outcome": "failed", "delivery_status": "failed", "delivered": [], "failed": failed, "level": level}
            return {"ok": True, "outcome": "succeeded", "delivery_status": "delivered", "delivered": delivered, "failed": [], "level": level}
        return await asyncio.to_thread(deliver)

    kernel.register(ActionContract(
        "notify_operator", risk_level="medium",
        required_parameters=("message",), handler=notify_operator))

    # Unified customer communication: one governed intent schema for email and
    # WhatsApp. Transport selection is bounded; providers never bypass the kernel.
    async def customer_communication(p, i):
        from services.customer_communication import send_customer_message
        channel = str(p.get("channel") or "").strip().lower()
        recipient = str(p.get("recipient") or "").strip()
        message = str(p.get("message") or "").strip()
        if channel == "whatsapp":
            recipient = str(p.get("to_number") or recipient).strip()
        result = await send_customer_message(
            channel=channel, recipient=recipient, message=message,
            subject=str(p.get("subject") or ""), html=bool(p.get("html", False)),
            social_account_id=str(p.get("social_account_id") or ""),
        )
        if result.get("ok"):
            result["communication_id"] = i.idempotency_key
        return result

    kernel.register(ActionContract(
        "customer_communication", risk_level="high",
        required_parameters=("channel", "recipient", "message"),
        requires_approval=True, handler=customer_communication))

    # Affiliate workload: reuse this same kernel instead of creating a second
    # affiliate action registry/execution plane.
    from services.affiliate_service import AffiliateService

    async def affiliate_create_product(p, i):
        service = AffiliateService(db)
        return {"ok": True, "product": service.create_product(
            workspace_id=i.workspace_id, name=p["name"], provider=p["provider"], program=p["program"],
            destination_domains=p["destination_domains"], commission_rate=p.get("commission_rate", 0),
            currency=p.get("currency", "USD"), metadata=p.get("metadata") or {})}

    async def affiliate_create_link(p, i):
        service = AffiliateService(db)
        return {"ok": True, "link": service.create_link(
            workspace_id=i.workspace_id, product_id=p["product_id"], destination_url=p["destination_url"],
            slug=p.get("slug"), campaign=p.get("campaign", ""), content_id=p.get("content_id"),
            metadata=p.get("metadata") or {})}

    async def affiliate_publish(p, i):
        from services.publish_orchestration import run_publish
        prepared = AffiliateService.prepare_promotion(
            p.get("caption", ""), disclosure_required=bool(p.get("disclosure_required", True)),
            disclosure_text=p.get("disclosure_text"))
        if not prepared["allowed"]:
            return {"ok": False, "outcome": "failed", "error": "affiliate disclosure policy blocked publication"}
        result = await run_publish(
            container=p.get("container"),
            workspace_id=i.workspace_id, brand_id=p.get("brand_id"), platform=p.get("platform"),
            topic=p.get("topic", "affiliate promotion"), caption=prepared["content"],
            source="affiliate_action", risk_level=i.risk_level, decision_id=i.decision_id,
            durable=False,
            image_url=p.get("image_url"),
            idempotency_key=i.idempotency_key)
        return result if isinstance(result, dict) else {"ok": False, "outcome": "failed", "error": "publish returned invalid result"}

    async def affiliate_pause(p, i):
        product_id = str(p["product_id"])
        result = db.table("affiliate_products").update({"status": "paused"}).eq("id", product_id).eq("workspace_id", i.workspace_id).execute()
        if not getattr(result, "data", None):
            return {"ok": False, "outcome": "failed", "error": "affiliate product not found"}
        return {"ok": True, "status": "paused", "product_id": product_id}

    async def affiliate_optimize(p, i):
        service = AffiliateService(db)
        analytics = service.analytics(workspace_id=i.workspace_id, days=int(p.get("days", 30)))
        # Optimization is deliberately advisory: it produces evidence and a
        # recommendation. It cannot silently pause/change revenue-producing assets.
        recommendation = "increase_testing" if analytics["clicks"] and analytics["conversions"] == 0 else "continue_monitoring"
        return {"ok": True, "recommendation": recommendation, "analytics": analytics}

    kernel.register(ActionContract(
        "affiliate_create_product", risk_level="low",
        required_parameters=("name", "provider", "program", "destination_domains"),
        handler=affiliate_create_product))
    kernel.register(ActionContract(
        "affiliate_create_link", risk_level="low",
        required_parameters=("product_id", "destination_url"), handler=affiliate_create_link))
    kernel.register(ActionContract(
        "affiliate_publish_promotion", risk_level="high",
        required_parameters=("platform", "caption"), requires_approval=True, handler=affiliate_publish))
    kernel.register(ActionContract(
        "affiliate_pause_product", risk_level="high",
        required_parameters=("product_id",), requires_approval=True, handler=affiliate_pause))
    kernel.register(ActionContract(
        "affiliate_optimize_campaign", risk_level="medium",
        required_parameters=(), handler=affiliate_optimize))

    # Content operating controls are governed actions too. Director may propose
    # schedule/mix changes, but it must not mutate ContentEngine state directly.
    async def content_adjust_mix(p, i):
        from services.content_engine import content_engine
        brand_id = str(p.get("brand_id") or "").strip()
        factor = float(p.get("factor", 0.0))
        duration_posts = int(p.get("duration_posts", 5))
        if not brand_id:
            return {"ok": False, "outcome": "failed", "error": "content_adjust_mix requires brand_id"}
        if factor > 0:
            result = await content_engine.boost_content(brand_id, factor=factor, duration_posts=duration_posts)
        else:
            result = await content_engine.reduce_content(brand_id, factor=abs(factor), duration_posts=duration_posts)
        return {"ok": True, "outcome": "succeeded", "result": result}

    kernel.register(ActionContract(
        "content_adjust_mix", risk_level="medium",
        required_parameters=("brand_id", "factor"),
        autonomous_retryable=True, handler=content_adjust_mix))

    # Canonical social/content publishing.  The Action Kernel is the governed
    # acceptance boundary; the canonical publishing pipeline remains the
    # provider-agnostic execution/reconciliation plane.  A DecisionGate ID is
    # mandatory so KCPG approval cannot silently mint publishing authority.
    #
    # Receipt contract: the handler receives the container via parameters and
    # executes the canonical pipeline inline (durable=False) so the kernel
    # receives a real receipt (platform_post_id) instead of a queue
    # acknowledgement.
    async def publish_content(p, i):
        from services.publish_orchestration import run_publish
        import base64

        decision_id = str(p.get("decision_id") or i.decision_id or "").strip()
        if not decision_id:
            return {"ok": False, "outcome": "failed", "error": "publish requires an explicit decision_id"}
        raw_image = p.get("image_bytes_b64")
        image_bytes = None
        if raw_image:
            try:
                image_bytes = base64.b64decode(raw_image, validate=True)
            except Exception:
                return {"ok": False, "outcome": "failed", "error": "invalid image_bytes_b64"}
        result = await run_publish(
            container=p.get("container"),
            workspace_id=i.workspace_id,
            brand_id=p.get("brand_id"),
            platform=p.get("platform"),
            topic=p.get("topic", ""),
            caption=p.get("caption", ""),
            source=p.get("source") or "kcpg_action_kernel",
            risk_level=i.risk_level,
            decision_id=decision_id,
            durable=False,
            idempotency_key=i.idempotency_key,
            image_url=p.get("image_url"),
            image_bytes=image_bytes,
            scheduled_post_id=p.get("scheduled_post_id"),
            automation_job_id=p.get("automation_job_id"),
            dry_run=bool(p.get("dry_run", False)),
        )
        return result if isinstance(result, dict) else {"ok": False, "outcome": "failed", "error": "publish returned invalid result"}

    kernel.register(ActionContract(
        "publish_content", risk_level="high",
        required_parameters=("platform", "caption", "decision_id"),
        requires_approval=True, handler=publish_content))

    # Public website/CMS publication is a governed external side effect. The
    # website connector is resolved by exact social-account ID and workspace,
    # then the connector receipt is promoted to the Action Kernel's provider
    # object-id contract. No website API call is reachable from automation
    # without passing this kernel boundary.
    async def website_publish(p, i):
        account_id = str(p.get("social_account_id") or "").strip()
        content = str(p.get("content") or p.get("caption") or "")
        if not account_id or not content.strip():
            return {"ok": False, "outcome": "failed", "error": "website_publish requires social_account_id and content"}
        from repositories.social_account_repository import SocialAccountRepository
        from services.connectors.website_connector import WebsiteConnector
        repo = SocialAccountRepository(db)
        account = repo.find_by_id(account_id)
        if account is None or str(account.workspace_id) != i.workspace_id:
            return {"ok": False, "outcome": "failed", "error": "Website account not found in workspace"}
        if not account.enabled:
            return {"ok": False, "outcome": "failed", "error": "Website account is disabled"}
        if str(account.platform).lower() != "website":
            return {"ok": False, "outcome": "failed", "error": "selected social account is not a website connector"}
        connector = WebsiteConnector(account)
        result = await asyncio.to_thread(
            connector.publish,
            content,
            image_url=p.get("image_url"),
            title=p.get("title"),
            slug=p.get("slug"),
            excerpt=p.get("excerpt"),
            status=p.get("status", "publish"),
            content_type=p.get("content_type"),
            published_at=p.get("published_at"),
            author=p.get("author"),
            category=p.get("category"),
            tags=p.get("tags"),
            canonical_url=p.get("canonical_url"),
            metadata=p.get("metadata"),
        )
        payload = {
            "ok": bool(getattr(result, "ok", False)),
            "outcome": getattr(result, "outcome", "failed") or "failed",
            "provider_object_id": getattr(result, "post_id", None),
            "platform": "website",
            "metadata": getattr(result, "metadata", {}) or {},
        }
        if getattr(result, "error", None):
            payload["error"] = result.error
        return payload

    async def website_update(p, i):
        account_id = str(p.get("social_account_id") or "").strip()
        post_id = str(p.get("post_id") or p.get("content_id") or "").strip()
        content = str(p.get("content") or p.get("caption") or "")
        if not account_id or not post_id:
            return {"ok": False, "outcome": "failed", "error": "website_update requires social_account_id and post_id"}
        mutable = {k: p.get(k) for k in ("content", "title", "slug", "excerpt", "status", "content_type", "published_at", "author", "category", "tags", "canonical_url", "metadata", "image_url") if p.get(k) is not None}
        if not any(v not in (None, "", [], {}) for v in mutable.values()):
            return {"ok": False, "outcome": "failed", "error": "website_update requires at least one mutable field"}
        from repositories.social_account_repository import SocialAccountRepository
        from services.connectors.website_connector import WebsiteConnector
        repo = SocialAccountRepository(db)
        account = repo.find_by_id(account_id)
        if account is None or str(account.workspace_id) != i.workspace_id:
            return {"ok": False, "outcome": "failed", "error": "Website account not found in workspace"}
        if not account.enabled:
            return {"ok": False, "outcome": "failed", "error": "Website account is disabled"}
        if str(account.platform).lower() != "website":
            return {"ok": False, "outcome": "failed", "error": "selected social account is not a website connector"}
        connector = WebsiteConnector(account)
        result = await asyncio.to_thread(
            connector.edit, post_id, content,
            title=p.get("title"),
            slug=p.get("slug"),
            excerpt=p.get("excerpt"),
            status=p.get("status"),
            content_type=p.get("content_type"),
            published_at=p.get("published_at"),
            author=p.get("author"),
            category=p.get("category"),
            tags=p.get("tags"),
            canonical_url=p.get("canonical_url"),
            metadata=p.get("metadata"),
        )
        payload = {
            "ok": bool(getattr(result, "ok", False)),
            "outcome": getattr(result, "outcome", "failed") or "failed",
            "provider_object_id": getattr(result, "post_id", None) or post_id,
            "platform": "website",
            "metadata": getattr(result, "metadata", {}) or {},
        }
        if getattr(result, "error", None):
            payload["error"] = result.error
        return payload

    kernel.register(ActionContract(
        "website_publish", risk_level="high",
        required_parameters=("social_account_id", "content"),
        requires_approval=True, handler=website_publish))
    kernel.register(ActionContract(
        "website_update", risk_level="high",
        required_parameters=("social_account_id", "post_id"),
        requires_approval=True, handler=website_update))
    return kernel
