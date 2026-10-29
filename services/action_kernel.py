"""
Governed Action Execution Kernel.

All consequential business actions enter here as typed ActionIntent objects.
The kernel performs contract validation, workspace scoping, approval/risk checks,
durable intent recording, and execution through the existing ExecutionLedger /
ExternalActionExecutor boundary.

This is deliberately provider-agnostic. Publishing remains compatible with its
canonical pipeline while business actions can now share the same governance
primitive without copying connector-specific logic.

Execution modes:
  - Durable: enqueue() -> approve() -> process_outbox_row(). Used by async
    callers (workers, schedulers) who want to receive acceptance immediately
    and have the side effect performed later by an outbox worker.
  - Inline: execute(). Used by callers who already hold a valid decision_id
    (Council / Director / Reflex) and want the real receipt in the same call.

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

v2 changes (2026-09-29):
  - ActionContract.inline_with_decision flag: registered contracts may declare
    that a present decision_id is sufficient to execute inline, bypassing the
    durable-admission requirement.
  - ActionKernel.execute() honors inline_with_decision so governed actions
    with a decision execute in the same call and return the real receipt.
    Without this, publish_content (requires_approval=True) was refused inline
    execution and every publish attempt surfaced as "Publish step failed".
  - approve() enforces the same scope/state guard on the memory path as it
    does on the DB path.
  - process_outbox_row fails closed when a durable row has no lease_token.
  - ActionResult output construction no longer overwrites real receipts with
    error fields when both are present.

v3 changes (2026-09-29):
  - Status vocabulary normalization. Handlers return different status
    vocabularies: the canonical pipeline uses "ok"/"error"/"uncertain",
    the connector uses "succeeded"/"failed", the durable worker may emit
    "in_progress". ActionResult.status must be normalized to the kernel's
    contract ("succeeded" / "failed" / "uncertain") before downstream
    consumers (PublishStep.async_run, workflow runners) branch on it.
    Without this, PublishStep raised a phantom "Publish step failed" after
    every successful Facebook post because the pipeline returned "ok" and
    PublishStep looked for "succeeded".
  - Provider object ID mirroring. Handlers name the provider identifier in
    several different keys (platform_post_id, provider_object_id, post_id).
    The kernel now mirrors the resolved value under the canonical "post_id"
    key on the output dict so downstream consumers (analytics, dashboards,
    webhook payloads) find it regardless of the handler's naming choice.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
import json
import logging
import uuid
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from services.execution_ledger import ExecutionLedger
from services.external_action_executor import ExternalActionExecutor

logger = logging.getLogger("kanyoza.action_kernel")

RISKS = {"low", "medium", "high", "critical"}

# ── Status vocabulary normalization tables ────────────────────────────────
# Handlers and connectors in this codebase use several different status
# vocabularies. The kernel normalizes all of them into the ActionResult
# contract vocabulary: "succeeded" / "failed" / "uncertain".
_STATUS_SUCCESS = frozenset({"ok", "success", "succeeded", "completed", "done"})
_STATUS_FAILURE = frozenset({"error", "failed", "failure", "rejected"})
_STATUS_UNCERTAIN = frozenset({"in_progress", "pending", "processing", "queued"})


def _normalize_status(raw: Any) -> str:
    """Map any handler status vocabulary into the kernel contract vocabulary."""
    value = str(raw or "").strip().lower()
    if value in _STATUS_SUCCESS:
        return "succeeded"
    if value in _STATUS_FAILURE:
        return "failed"
    if value in _STATUS_UNCERTAIN:
        return "uncertain"
    return value or "uncertain"


def _mirror_post_id(output: dict, result: dict) -> None:
    """Mirror the provider object ID under the canonical "post_id" key.

    Different handlers name the provider identifier differently:
      - canonical pipeline: "platform_post_id"
      - connector results:   "provider_object_id" / "post_id"
      - raw executor:        "provider_object_id"

    Downstream consumers (RecordAnalyticsStep, dashboards, webhook payloads)
    expect "post_id". This helper ensures all names are present when any one
    of them is set, without overwriting a real value.
    """
    if not isinstance(output, dict):
        return
    provider_post_id = (
        output.get("post_id")
        or output.get("platform_post_id")
        or output.get("provider_object_id")
        or result.get("provider_object_id")
        or result.get("platform_post_id")
    )
    if not provider_post_id:
        return
    output.setdefault("post_id", provider_post_id)
    output.setdefault("platform_post_id", provider_post_id)


# ═══════════════════════════════════════════════════════════════════════════
# Dataclasses
# ═══════════════════════════════════════════════════════════════════════════


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
    # When True and a decision_id is present on the intent, execute() will run
    # inline instead of returning "approval_required". Use this for governed
    # actions whose callers already hold a valid authorization and need the
    # real receipt in the same call (e.g. publish_content).
    #
    # When False (default), a governed action with requires_approval=True or
    # irreversible=True must go through enqueue()/approve()/process_outbox_row()
    # even if the caller holds a decision_id.
    inline_with_decision: bool = False
    handler: Optional[Callable[..., Any]] = None
    reconciler: Optional[Callable[..., Any]] = None
    autonomous_retryable: bool = False

    def validate(self, intent: ActionIntent) -> None:
        if intent.action_type != self.action_type:
            raise ValueError("action contract mismatch")
        if intent.risk_level != self.risk_level:
            raise ValueError(
                f"risk mismatch for {self.action_type}: "
                f"intent={intent.risk_level} contract={self.risk_level}"
            )
        missing = [
            k for k in self.required_parameters
            if intent.parameters.get(k) in (None, "")
        ]
        if missing:
            raise ValueError(
                f"{self.action_type} missing required parameters: "
                f"{', '.join(missing)}"
            )


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


# ═══════════════════════════════════════════════════════════════════════════
# Kernel
# ═══════════════════════════════════════════════════════════════════════════


class ActionKernel:
    """Single governed execution boundary for typed business actions."""

    def __init__(self, db=None, *, allow_in_memory: bool = False):
        self.db = db
        self.allow_in_memory = allow_in_memory
        self.ledger = ExecutionLedger(db=db, allow_in_memory=allow_in_memory)
        self.executor = ExternalActionExecutor(self.ledger)
        self._contracts: dict[str, ActionContract] = {}
        self._memory_intents: dict[str, dict[str, Any]] = {}

    def get_contract_metadata(self, action_type: str) -> Optional[dict[str, Any]]:
        """Return read-only metadata for a registered action contract.

        This exposes governance metadata without exposing handlers or granting
        callers any execution capability.
        """
        contract = self._contracts.get(str(action_type or "").strip())
        if contract is None:
            return None
        return {
            "action_type": contract.action_type,
            "risk_level": contract.risk_level,
            "required_parameters": list(contract.required_parameters),
            "irreversible": bool(contract.irreversible),
            "requires_approval": bool(contract.requires_approval),
            "inline_with_decision": bool(contract.inline_with_decision),
            "autonomous_retryable": bool(contract.autonomous_retryable),
            "has_reconciler": callable(contract.reconciler),
        }

    def registered_action_types(self) -> tuple[str, ...]:
        """Return the canonical registered action names without execution authority."""
        return tuple(sorted(self._contracts))

    def register(self, contract: ActionContract) -> None:
        if contract.action_type in self._contracts:
            raise ValueError(f"action already registered: {contract.action_type}")
        if contract.risk_level not in RISKS:
            raise ValueError("invalid contract risk")
        if not callable(contract.handler):
            raise ValueError("action contract requires a callable handler")
        self._contracts[contract.action_type] = contract

    # ── Parameter hygiene ─────────────────────────────────────────────

    @staticmethod
    def _split_params(
        parameters: Optional[dict[str, Any]]
    ) -> tuple[dict[str, Any], Any]:
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
    def make_idempotency_key(
        workspace_id: str,
        action_type: str,
        parameters: dict[str, Any],
        supplied: str = "",
    ) -> str:
        if supplied:
            return supplied
        canonical = json.dumps(
            parameters or {}, sort_keys=True, separators=(",", ":"), default=str
        )
        return ExecutionLedger.make_idempotency_key(
            workspace_id, action_type, canonical
        )

    # ── Durable intent persistence ────────────────────────────────────

    def _persist_intent(
        self, intent_id: str, intent: ActionIntent, *, status: str = "pending"
    ) -> None:
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
            self.db.table("action_outbox").upsert(
                row, on_conflict="workspace_id,idempotency_key"
            ).execute()
        except TypeError:
            # Older Supabase clients don't support on_conflict.
            try:
                self.db.table("action_outbox").upsert(row).execute()
            except Exception:
                raise RuntimeError("durable action intent persistence failed")
        except Exception:
            # A durable intent is part of the safety boundary. Never execute a
            # side effect if its authoritative outbox record could not be stored.
            raise RuntimeError("durable action intent persistence failed")

    def _update_intent(
        self, intent_id: str, workspace_id: str, **fields: Any
    ) -> None:
        if self.db is None:
            row = self._memory_intents.get(intent_id)
            if row:
                row.update(fields)
            return
        try:
            self.db.table("action_outbox").update(fields).eq(
                "intent_id", intent_id
            ).eq("workspace_id", workspace_id).execute()
        except Exception:
            logger.exception(
                "action outbox finalization failed intent=%s", intent_id
            )
            raise RuntimeError("durable action intent finalization failed")

    def _build_intent(
        self,
        *,
        workspace_id: str,
        action_type: str,
        parameters: Optional[dict[str, Any]],
        risk_level: str,
        decision_id: Optional[str],
        correlation_id: Optional[str],
        idempotency_key: Optional[str],
        source: str,
    ) -> tuple[ActionIntent, ActionContract, str]:
        # Strip runtime refs before deepcopy, then reattach them so the handler
        # can access the container during execution.
        safe_params, container_ref = self._split_params(parameters)
        params = copy.deepcopy(safe_params)
        self._reattach(params, container_ref)

        key = self.make_idempotency_key(
            workspace_id, action_type, params, idempotency_key or ""
        )
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
        intent_id = "act_" + hashlib.sha256(
            f"{intent.workspace_id}|{intent.idempotency_key}".encode()
        ).hexdigest()[:24]
        return intent, contract, intent_id

    # ── Shared result post-processing ─────────────────────────────────

    @staticmethod
    def _extract_output(result: dict[str, Any]) -> dict[str, Any]:
        """Extract the handler output dict from an executor result.

        Consolidates the priority chain the kernel has always used:
          1. result["output"] when it's already a dict
          2. result["result"] when it's already a dict (executor's fallback)
          3. all top-level keys except the executor envelope keys
        Then mirrors the provider object ID under "post_id".
        """
        output = result.get("output") if isinstance(result.get("output"), dict) else {}
        if not output and isinstance(result.get("result"), dict):
            output = copy.deepcopy(result["result"])
        if not output:
            output = {
                k: v
                for k, v in result.items()
                if k not in {"status", "execution_id", "provider_object_id", "replayed"}
            }
        if result.get("error") and "error" not in output:
            output["error"] = result.get("error")
        if result.get("receipt") is not None:
            output.setdefault("receipt", copy.deepcopy(result.get("receipt")))
        _mirror_post_id(output, result)
        return output

    # ── Enqueue / approve (durable, async callers) ────────────────────

    async def enqueue(
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
    ) -> dict[str, Any]:
        """Durably accept an action without performing its external side effect.

        This is the preferred boundary for asynchronous callers. The caller
        receives acceptance only after the authoritative action_outbox row
        exists.
        """
        # Strip runtime refs before persistence — they don't belong in the DB.
        safe_params, _container_ref = self._split_params(parameters)
        intent, contract, intent_id = self._build_intent(
            workspace_id=workspace_id,
            action_type=action_type,
            parameters=safe_params,
            risk_level=risk_level,
            decision_id=decision_id,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            source=source,
        )
        governed = (
            contract.requires_approval
            or contract.irreversible
            or intent.risk_level in {"high", "critical"}
        )
        if governed and not intent.decision_id:
            status = "governance_required"
        elif governed:
            status = "approval_required"
        else:
            status = "pending"

        if self.db is None:
            existing = self._memory_intents.get(intent_id)
            if existing:
                return {
                    "ok": True,
                    "status": existing.get("status", "pending"),
                    "intent_id": intent_id,
                    "action_type": intent.action_type,
                    "idempotency_key": intent.idempotency_key,
                    "accepted": existing.get("status") == "pending",
                }
            self._persist_intent(intent_id, intent, status=status)
            self._memory_intents[intent_id]["approved"] = False
        else:
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
                "approved": False,
            }
            try:
                inserted = await asyncio.to_thread(
                    lambda: self.db.table("action_outbox").insert(row).execute()
                )
                if not getattr(inserted, "data", None):
                    raise RuntimeError(
                        "durable action intent insert returned no authoritative row"
                    )
            except Exception as exc:
                # A uniqueness conflict is normal replay; recover the
                # authoritative existing row rather than resetting its
                # terminal state. Other DB failures remain fail-closed.
                try:
                    existing = await asyncio.to_thread(
                        lambda: self.db.table("action_outbox")
                        .select("*")
                        .eq("workspace_id", intent.workspace_id)
                        .eq("idempotency_key", intent.idempotency_key)
                        .maybe_single()
                        .execute()
                    )
                    existing_row = getattr(existing, "data", None)
                    if not existing_row:
                        raise RuntimeError(
                            "durable action intent persistence failed"
                        ) from exc
                    return {
                        "ok": True,
                        "status": existing_row.get("status", "pending"),
                        "intent_id": existing_row.get("intent_id", intent_id),
                        "action_type": intent.action_type,
                        "idempotency_key": intent.idempotency_key,
                        "accepted": existing_row.get("status") == "pending",
                    }
                except Exception:
                    raise RuntimeError(
                        "durable action intent persistence failed"
                    ) from exc
        return {
            "ok": True,
            "status": status,
            "intent_id": intent_id,
            "action_type": intent.action_type,
            "idempotency_key": intent.idempotency_key,
            "accepted": status == "pending",
        }

    async def approve(
        self,
        *,
        workspace_id: str,
        intent_id: str,
        approver_id: str = "",
        approver_role: str = "",
        approval_source: str = "unknown",
    ) -> dict[str, Any]:
        """Approve a previously accepted high-impact intent without executing it inline."""
        ws = str(workspace_id or "").strip()
        iid = str(intent_id or "").strip()
        approver = str(approver_id or "").strip()
        role = str(approver_role or "").strip().lower()
        source = str(approval_source or "unknown").strip()
        if not ws or not iid:
            raise ValueError("workspace_id and intent_id are required")
        if not approver:
            raise ValueError("approver_id is required")
        if role not in {"operator", "admin", "owner", "species"}:
            raise ValueError("operator authorization required for approval")

        if self.db is None:
            row = self._memory_intents.get(iid)
            if not row or row.get("workspace_id") != ws:
                raise ValueError("action intent not found")
            if row.get("status") != "approval_required":
                raise ValueError("action intent not found or not awaiting approval")
            row["approved"] = True
            row["approved_by"] = approver
            row["approved_role"] = role
            row["approved_at"] = datetime.now(timezone.utc).isoformat()
            row["approval_source"] = source
            row["status"] = "pending"
            return {
                "ok": True,
                "status": "pending",
                "intent_id": iid,
                "approved_by": approver,
                "approved_role": role,
            }

        result = await asyncio.to_thread(
            lambda: self.db.table("action_outbox")
            .update(
                {
                    "approved": True,
                    "status": "pending",
                    "approved_by": approver,
                    "approved_role": role,
                    "approved_at": datetime.now(timezone.utc).isoformat(),
                    "approval_source": source,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            .eq("intent_id", iid)
            .eq("workspace_id", ws)
            .eq("status", "approval_required")
            .execute()
        )
        if not getattr(result, "data", None):
            raise ValueError("action intent not found or not awaiting approval")
        return {
            "ok": True,
            "status": "pending",
            "intent_id": iid,
            "approved_by": approver,
            "approved_role": role,
        }

    # ── Outbox worker entry point ─────────────────────────────────────

    async def process_outbox_row(self, row: dict[str, Any]) -> dict[str, Any]:
        """Execute one worker-claimed durable intent through the same kernel boundary."""
        intent, contract, intent_id = self._build_intent(
            workspace_id=row.get("workspace_id"),
            action_type=row.get("action_type"),
            parameters=row.get("parameters") or {},
            risk_level=row.get("risk_level") or "low",
            decision_id=row.get("decision_id"),
            correlation_id=row.get("correlation_id"),
            idempotency_key=row.get("idempotency_key"),
            source=row.get("source") or "action_outbox_worker",
        )
        approved = bool(row.get("approved", False))
        approval_authority = str(row.get("approved_by") or "").strip()
        approval_role = str(row.get("approved_role") or "").strip().lower()
        approval_at = str(row.get("approved_at") or "").strip()
        governed = (
            contract.requires_approval
            or contract.irreversible
            or intent.risk_level in {"high", "critical"}
        )
        if governed:
            if not intent.decision_id:
                self._update_intent(
                    intent_id, intent.workspace_id, status="governance_required"
                )
                return ActionResult(
                    "governance_required",
                    intent.action_type,
                    str(row.get("execution_id") or ""),
                    intent_id,
                ).to_dict()
            if not (
                approved
                and approval_authority
                and approval_role in {"operator", "admin", "owner", "species"}
                and approval_at
            ):
                self._update_intent(
                    intent_id, intent.workspace_id, status="approval_required"
                )
                return ActionResult(
                    "approval_required",
                    intent.action_type,
                    str(row.get("execution_id") or ""),
                    intent_id,
                ).to_dict()

        # Fail-closed: a durable row must carry a lease token. Without one,
        # there is no fencing guarantee and we must not execute.
        lease_token = row.get("lease_token")
        if self.db is not None and not lease_token:
            raise RuntimeError(
                "process_outbox_row requires a lease_token for durable execution"
            )

        self._update_intent(intent_id, intent.workspace_id, status="processing")
        operation = lambda: contract.handler(intent.parameters, intent)
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
                **({"intelligence_provenance": intent.parameters.get("_kdo_intelligence")}
                   if isinstance(intent.parameters.get("_kdo_intelligence"), dict) else {}),
            },
        )

        # ── Status normalization (v3) ──────────────────────────────────
        # The executor may hand us "ok", "succeeded", "in_progress", or any
        # other vocabulary the handler decided to use. Normalize to the
        # ActionResult contract before branching.
        raw_status = result.get("status")
        normalized = _normalize_status(raw_status)
        # Preserve the executor's in_progress signal — it is a live ownership
        # signal, not a terminal status. Everything else maps through.
        if str(raw_status or "").strip().lower() == "in_progress":
            return ActionResult(
                status="in_progress",
                action_type=intent.action_type,
                execution_id=str(result.get("execution_id") or ""),
                intent_id=intent_id,
                output=self._extract_output(result),
                reconciliation_required=False,
                replayed=bool(result.get("replayed")),
            ).to_dict()

        output = self._extract_output(result)

        reconciliation = normalized == "uncertain" or bool(
            result.get("reconciliation_required")
        )
        final_status = "reconciliation_required" if reconciliation else normalized

        if self.db is not None and lease_token:
            finalized = await asyncio.to_thread(
                lambda: self.db.rpc(
                    "finalize_action_outbox",
                    {
                        "p_intent_id": intent_id,
                        "p_workspace_id": intent.workspace_id,
                        "p_lease_token": lease_token,
                        "p_status": final_status,
                        "p_execution_id": result.get("execution_id"),
                        "p_provider_object_id": result.get("provider_object_id"),
                        "p_output": output,
                        "p_error": result.get("error"),
                    },
                ).execute()
            )
            if not getattr(finalized, "data", False):
                return ActionResult(
                    "uncertain",
                    intent.action_type,
                    str(result.get("execution_id") or ""),
                    intent_id,
                    output,
                    True,
                    bool(result.get("replayed")),
                ).to_dict()
        else:
            self._update_intent(
                intent_id,
                intent.workspace_id,
                status=final_status,
                execution_id=result.get("execution_id"),
                provider_object_id=result.get("provider_object_id"),
                output=output,
                error=result.get("error"),
            )

        return ActionResult(
            status=normalized,
            action_type=intent.action_type,
            execution_id=str(result.get("execution_id") or ""),
            intent_id=intent_id,
            output=output,
            reconciliation_required=reconciliation,
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
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Execute a governed action inline and return the real receipt.

        Inline execution is permitted when either:
          1. The action is NOT governed (no requires_approval, not irreversible,
             risk_level not in {high, critical}), OR
          2. The action is governed AND the contract declares
             inline_with_decision=True AND a non-empty decision_id is present.

        In every other case, the caller must go through the durable
        enqueue() -> approve() -> process_outbox_row() path. This is the
        safety boundary: a governed action with no decision is refused, and
        a governed action with a decision but no inline_with_decision opt-in
        is still refused inline.
        """
        # Strip non-serializable refs before deepcopy, then reattach them for
        # the handler. Without this, a live container (with thread locks and
        # httpx sessions) breaks copy.deepcopy().
        safe_params, container_ref = self._split_params(parameters)
        params = copy.deepcopy(safe_params)
        self._reattach(params, container_ref)

        key = self.make_idempotency_key(
            workspace_id, action_type, params, idempotency_key or ""
        )
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

        governed = (
            contract.requires_approval
            or contract.irreversible
            or intent.risk_level in {"high", "critical"}
        )
        if governed:
            if not contract.inline_with_decision:
                # Contract does not opt in to inline execution. Callers must
                # use the durable enqueue/approve path.
                status = (
                    "governance_required"
                    if not intent.decision_id
                    else "approval_required"
                )
                return ActionResult(
                    status=status,
                    action_type=intent.action_type,
                    execution_id="",
                    intent_id="",
                    output={
                        "reason": (
                            "governed action must be durably admitted and "
                            "approved before execution"
                        )
                    },
                ).to_dict()
            if not intent.decision_id:
                # inline_with_decision is True, but no decision was supplied.
                return ActionResult(
                    status="governance_required",
                    action_type=intent.action_type,
                    execution_id="",
                    intent_id="",
                    output={
                        "reason": (
                            "governed action requires a decision_id for "
                            "inline execution"
                        )
                    },
                ).to_dict()
            # Fall through: decision present and contract opted in to inline.

        intent_id = "act_" + hashlib.sha256(
            f"{intent.workspace_id}|{intent.idempotency_key}".encode()
        ).hexdigest()[:24]
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
                    **({"intelligence_provenance": intent.parameters.get("_kdo_intelligence")}
                       if isinstance(intent.parameters.get("_kdo_intelligence"), dict) else {}),
                    **(metadata or {}),
                },
            )
        except Exception as exc:
            self._update_intent(
                intent_id,
                intent.workspace_id,
                status="failed",
                error=str(exc)[:1000],
            )
            raise

        # ── Status normalization (v3) ──────────────────────────────────
        # Handlers in this codebase return several different status
        # vocabularies: the canonical pipeline uses "ok"/"error"/"uncertain",
        # the connector uses "succeeded"/"failed", the durable worker may
        # emit "in_progress". ActionResult.status must be one of "succeeded"
        # / "failed" / "uncertain" so downstream consumers
        # (PublishStep.async_run, workflow runners) branch on a single
        # vocabulary. Without normalization, PublishStep saw "ok" and
        # raised a phantom "Publish step failed" after every successful
        # Facebook post.
        status = _normalize_status(result.get("status"))

        output = self._extract_output(result)

        reconciliation = status == "uncertain" or bool(
            result.get("reconciliation_required")
        )
        final_status = "reconciliation_required" if reconciliation else status

        try:
            self._update_intent(
                intent_id,
                intent.workspace_id,
                status=final_status,
                execution_id=result.get("execution_id"),
                provider_object_id=result.get("provider_object_id"),
                output=output,
                error=result.get("error"),
            )
        except Exception:
            # ExternalActionExecutor already converts receipt persistence
            # failure into uncertainty. If the intent projection itself fails,
            # retain the conservative execution result and never turn it into
            # success.
            if status == "succeeded":
                return ActionResult(
                    status="uncertain",
                    action_type=intent.action_type,
                    execution_id=str(result.get("execution_id") or ""),
                    intent_id=intent_id,
                    output=output,
                    reconciliation_required=True,
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


# ═══════════════════════════════════════════════════════════════════════════
# Default kernel
# ═══════════════════════════════════════════════════════════════════════════


def build_default_action_kernel(
    db=None, *, allow_in_memory: bool = False, plugin=None, it_repository=None, render_adapter=None, finance_repository=None, stripe_adapter=None, crm_repository=None, twilio_adapter=None, education_repository=None, healthcare_repository=None, commerce_repository=None, github_adapter=None, manufacturing_repository=None, jira_adapter=None, government_repository=None, government_notification_repository=None, finance_statement_repository=None, energy_repository=None, marketing_repository=None, cybersecurity_repository=None, supply_chain_repository=None, agriculture_repository=None
) -> ActionKernel:
    """Register the first governed business-action family (CRM)."""
    from plugins.industries.crm.plugin import CRMPlugin

    plugin = plugin or CRMPlugin(db)

    async def run_crm(
        command: str, parameters: dict[str, Any], intent: ActionIntent
    ) -> dict[str, Any]:
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
            return {
                "ok": False,
                "outcome": "failed",
                "error": value.get("error") or "CRM action failed",
            }

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

    # ── IT deployment bridge (dependency-injected; never auto-discovers credentials) ──
    if it_repository is not None and render_adapter is not None:
        from services.sector_it_deployment_execution import register_it_deployment_action
        register_it_deployment_action(kernel, it_repository, render_adapter)

    # ── Finance payment bridge (dependency-injected; provider semantics remain uncertain until reconciliation) ──
    if finance_repository is not None and stripe_adapter is not None:
        from services.sector_finance_payment_execution import register_finance_payment_action
        register_finance_payment_action(kernel, finance_repository, stripe_adapter)

    if finance_statement_repository is not None and twilio_adapter is not None:
        from services.sector_finance_statement_execution import register_finance_statement_action
        register_finance_statement_action(kernel, finance_statement_repository, twilio_adapter)

    # ── CRM governed outbound messaging bridge ─────────────────────────
    if crm_repository is not None and twilio_adapter is not None:
        from services.sector_crm_message_execution import register_crm_message_action
        register_crm_message_action(kernel, crm_repository, twilio_adapter)

    # -- Education parent-message bridge (dependency-injected) -----------
    if education_repository is not None and twilio_adapter is not None:
        from services.sector_education_parent_message_execution import register_education_parent_message_action
        register_education_parent_message_action(kernel, education_repository, twilio_adapter)
    if healthcare_repository is not None and twilio_adapter is not None:
        from services.sector_healthcare_patient_message_execution import register_healthcare_patient_message_action
        register_healthcare_patient_message_action(kernel, healthcare_repository, twilio_adapter)
    if commerce_repository is not None and twilio_adapter is not None:
        from services.sector_commerce_order_notification_execution import register_commerce_order_notification_action
        register_commerce_order_notification_action(kernel, commerce_repository, twilio_adapter)
    if commerce_repository is not None:
        from services.sector_commerce_internal_record_execution import register_commerce_internal_record_actions
        register_commerce_internal_record_actions(kernel, commerce_repository)
    if marketing_repository is not None:
        from services.sector_marketing_schedule_execution import register_marketing_schedule_content_action
        register_marketing_schedule_content_action(kernel, marketing_repository)
    if cybersecurity_repository is not None:
        from services.sector_security_internal_record_execution import register_security_internal_record_actions
        register_security_internal_record_actions(kernel, cybersecurity_repository)
    from services.sector_internal_record_execution import register_sector_internal_record_actions
    register_sector_internal_record_actions(
        kernel, crm_repository=crm_repository, education_repository=education_repository,
        healthcare_repository=healthcare_repository, supply_chain_repository=supply_chain_repository,
        energy_repository=energy_repository, government_repository=government_repository,
        agriculture_repository=agriculture_repository,
    )
    if commerce_repository is not None and stripe_adapter is not None:
        from services.sector_commerce_capture_payment_execution import register_commerce_capture_payment_action
        register_commerce_capture_payment_action(kernel, commerce_repository, stripe_adapter)
    if it_repository is not None and github_adapter is not None:
        from services.sector_it_service_remediation_execution import register_it_service_remediation_action
        register_it_service_remediation_action(kernel, it_repository, github_adapter)
    if government_repository is not None and government_notification_repository is not None and twilio_adapter is not None:
        from services.sector_government_applicant_notification_execution import register_government_applicant_notification_action
        register_government_applicant_notification_action(kernel, government_repository, government_notification_repository, twilio_adapter)

    if manufacturing_repository is not None and jira_adapter is not None:
        from services.sector_manufacturing_maintenance_execution import register_manufacturing_maintenance_action
        register_manufacturing_maintenance_action(kernel, manufacturing_repository, jira_adapter)
    if government_repository is not None and jira_adapter is not None:
        from services.sector_government_field_service_execution import register_government_field_service_action
        register_government_field_service_action(kernel, government_repository, jira_adapter)
    if energy_repository is not None and jira_adapter is not None:
        from services.sector_energy_crew_dispatch_execution import register_energy_crew_dispatch_action
        register_energy_crew_dispatch_action(kernel, energy_repository, jira_adapter)

    # ── CRM ────────────────────────────────────────────────────────────
    kernel.register(
        ActionContract(
            "crm_add_contact",
            risk_level="medium",
            required_parameters=("name",),
            requires_approval=False,
            autonomous_retryable=True,
            handler=lambda p, i: run_crm(
                "add_contact",
                {
                    "name": p.get("name"),
                    "phone": p.get("phone", ""),
                    "email": p.get("email"),
                    "company": p.get("company", ""),
                    "status": p.get("status", "lead"),
                },
                i,
            ),
        )
    )
    kernel.register(
        ActionContract(
            "crm_add_deal",
            risk_level="high",
            required_parameters=("amount", "contact"),
            requires_approval=True,
            handler=lambda p, i: run_crm(
                "add_deal",
                {
                    "amount": p.get("amount"),
                    "contact": p.get("contact"),
                    "stage": p.get("stage", "lead"),
                    "date": p.get("date"),
                },
                i,
            ),
        )
    )
    kernel.register(
        ActionContract(
            "crm_update_contact_status",
            risk_level="medium",
            required_parameters=("contact_id", "status"),
            autonomous_retryable=True,
            handler=lambda p, i: run_crm(
                "update_contact_status",
                {"contact_id": p.get("contact_id"), "status": p.get("status")},
                i,
            ),
        )
    )
    kernel.register(
        ActionContract(
            "crm_update_deal_stage",
            risk_level="medium",
            required_parameters=("deal_id", "stage"),
            autonomous_retryable=True,
            handler=lambda p, i: run_crm(
                "update_deal_stage",
                {"deal_id": p.get("deal_id"), "stage": p.get("stage")},
                i,
            ),
        )
    )
    kernel.register(
        ActionContract(
            "crm_schedule_followup",
            risk_level="medium",
            required_parameters=("contact", "date"),
            autonomous_retryable=True,
            handler=lambda p, i: run_crm(
                "schedule_followup",
                {
                    "contact": p.get("contact"),
                    "date": p.get("date"),
                    "notes": p.get("notes", ""),
                },
                i,
            ),
        )
    )
    kernel.register(
        ActionContract(
            "crm_add_meeting",
            risk_level="medium",
            required_parameters=("title",),
            autonomous_retryable=True,
            handler=lambda p, i: run_crm(
                "add_meeting",
                {
                    "title": p.get("title"),
                    "description": p.get("description"),
                    "date": p.get("date", ""),
                    "time": p.get("time"),
                    "location": p.get("location", "Virtual"),
                },
                i,
            ),
        )
    )

    # ── Customer communication (WhatsApp) ──────────────────────────────
    async def whatsapp_message(p, i):
        account_id = str(p.get("social_account_id") or "").strip()
        recipient = str(p.get("to_number") or p.get("recipient") or "").strip()
        message = str(p.get("message") or "").strip()
        if not account_id or not recipient or not message:
            return {
                "ok": False,
                "outcome": "failed",
                "error": (
                    "whatsapp_message requires social_account_id, "
                    "recipient, and message"
                ),
            }
        from repositories.social_account_repository import SocialAccountRepository
        from services.connectors.whatsapp_connector import WhatsAppConnector

        repo = SocialAccountRepository(db)
        account = repo.find_by_id(account_id)
        if account is None or str(account.workspace_id) != i.workspace_id:
            return {
                "ok": False,
                "outcome": "failed",
                "error": "WhatsApp account not found in workspace",
            }
        if not account.enabled:
            return {
                "ok": False,
                "outcome": "failed",
                "error": "WhatsApp account is disabled",
            }
        if str(account.platform).lower() != "whatsapp":
            return {
                "ok": False,
                "outcome": "failed",
                "error": "selected social account is not WhatsApp",
            }
        connector = WhatsAppConnector(account)
        result = await asyncio.to_thread(
            connector.publish, message, to_number=recipient
        )
        if not getattr(result, "ok", False):
            outcome = getattr(result, "outcome", "failed") or "failed"
            return {
                "ok": False,
                "outcome": outcome,
                "error": getattr(result, "error", None) or "WhatsApp send failed",
            }
        return {
            "ok": True,
            "outcome": "succeeded",
            "provider_message_id": getattr(result, "post_id", ""),
            "recipient": recipient,
            "platform": "whatsapp",
        }

    kernel.register(
        ActionContract(
            "whatsapp_message",
            risk_level="high",
            required_parameters=("social_account_id", "to_number", "message"),
            requires_approval=True,
            handler=whatsapp_message,
        )
    )

    # ── Customer communication (Facebook) ──────────────────────────────
    async def facebook_message(p, i):
        recipient = str(p.get("recipient_id") or "").strip()
        message = str(p.get("message") or "").strip()
        account_id = str(p.get("social_account_id") or "").strip()
        if not recipient or not message or not account_id:
            return {
                "ok": False,
                "outcome": "failed",
                "error": (
                    "facebook_message requires social_account_id, "
                    "recipient_id and message"
                ),
            }
        from repositories.social_account_repository import SocialAccountRepository
        from services.connectors.connector_factory import create_connector

        account = SocialAccountRepository(db).find_by_id(account_id)
        if account is None or str(account.workspace_id) != i.workspace_id:
            return {
                "ok": False,
                "outcome": "failed",
                "error": "Facebook account not found in workspace",
            }
        if not account.enabled or str(account.platform).lower() != "facebook":
            return {
                "ok": False,
                "outcome": "failed",
                "error": "selected social account is not Facebook",
            }
        connector = create_connector(account)
        result = await asyncio.to_thread(connector.send_message, recipient, message)
        if not getattr(result, "ok", False):
            return {
                "ok": False,
                "outcome": getattr(result, "outcome", "failed") or "failed",
                "error": getattr(result, "error", None)
                or "Facebook message transport rejected the send",
            }
        return {
            "ok": True,
            "outcome": "succeeded",
            "provider_message_id": getattr(result, "post_id", ""),
            "recipient_id": recipient,
        }

    kernel.register(
        ActionContract(
            "facebook_message",
            risk_level="medium",
            required_parameters=("social_account_id", "recipient_id", "message"),
            handler=facebook_message,
        )
    )

    # ── Operator notification ──────────────────────────────────────────
    async def notify_operator(p, i):
        message = str(p.get("message") or "").strip()
        if not message:
            return {
                "ok": False,
                "outcome": "failed",
                "error": "notify_operator requires message",
            }
        level = str(p.get("level") or "info").lower()
        if level not in {"info", "warning", "error", "critical"}:
            return {
                "ok": False,
                "outcome": "failed",
                "error": "invalid notification level",
            }
        channels = p.get("channels") or ["slack"]
        allowed = {"slack", "discord", "email"}
        if not isinstance(channels, list) or not set(channels).issubset(allowed):
            return {
                "ok": False,
                "outcome": "failed",
                "error": "channels must be a subset of slack, discord, email",
            }
        from services.notifier import notifier

        def deliver():
            results = notifier.alert(message, level=level, channels=channels)
            delivered = [name for name in channels if bool(results.get(name))]
            failed = [name for name in channels if not bool(results.get(name))]
            if delivered and failed:
                return {
                    "ok": False,
                    "outcome": "partial",
                    "delivery_status": "partial",
                    "delivered": delivered,
                    "failed": failed,
                    "level": level,
                    "reconciliation_required": True,
                }
            if not delivered:
                return {
                    "ok": False,
                    "outcome": "failed",
                    "delivery_status": "failed",
                    "delivered": [],
                    "failed": failed,
                    "level": level,
                }
            return {
                "ok": True,
                "outcome": "succeeded",
                "delivery_status": "delivered",
                "delivered": delivered,
                "failed": [],
                "level": level,
            }

        return await asyncio.to_thread(deliver)

    kernel.register(
        ActionContract(
            "notify_operator",
            risk_level="medium",
            required_parameters=("message",),
            handler=notify_operator,
        )
    )

    # ── Unified customer communication ─────────────────────────────────
    async def customer_communication(p, i):
        from services.customer_communication import send_customer_message

        channel = str(p.get("channel") or "").strip().lower()
        recipient = str(p.get("recipient") or "").strip()
        message = str(p.get("message") or "").strip()
        if channel == "whatsapp":
            recipient = str(p.get("to_number") or recipient).strip()
        result = await send_customer_message(
            channel=channel,
            recipient=recipient,
            message=message,
            subject=str(p.get("subject") or ""),
            html=bool(p.get("html", False)),
            social_account_id=str(p.get("social_account_id") or ""),
        )
        if result.get("ok"):
            result["communication_id"] = i.idempotency_key
        return result

    kernel.register(
        ActionContract(
            "customer_communication",
            risk_level="high",
            required_parameters=("channel", "recipient", "message"),
            requires_approval=True,
            handler=customer_communication,
        )
    )

    # ── Affiliate workload ─────────────────────────────────────────────
    from services.affiliate_service import AffiliateService

    async def affiliate_create_product(p, i):
        service = AffiliateService(db)
        return {
            "ok": True,
            "product": service.create_product(
                workspace_id=i.workspace_id,
                name=p["name"],
                provider=p["provider"],
                program=p["program"],
                destination_domains=p["destination_domains"],
                commission_rate=p.get("commission_rate", 0),
                currency=p.get("currency", "USD"),
                metadata=p.get("metadata") or {},
            ),
        }

    async def affiliate_create_link(p, i):
        service = AffiliateService(db)
        return {
            "ok": True,
            "link": service.create_link(
                workspace_id=i.workspace_id,
                product_id=p["product_id"],
                destination_url=p["destination_url"],
                slug=p.get("slug"),
                campaign=p.get("campaign", ""),
                content_id=p.get("content_id"),
                metadata=p.get("metadata") or {},
            ),
        }

    async def affiliate_publish(p, i):
        from services.publish_orchestration import run_publish

        prepared = AffiliateService.prepare_promotion(
            p.get("caption", ""),
            disclosure_required=bool(p.get("disclosure_required", True)),
            disclosure_text=p.get("disclosure_text"),
        )
        if not prepared["allowed"]:
            return {
                "ok": False,
                "outcome": "failed",
                "error": "affiliate disclosure policy blocked publication",
            }
        result = await run_publish(
            container=p.get("container"),
            workspace_id=i.workspace_id,
            brand_id=p.get("brand_id"),
            platform=p.get("platform"),
            topic=p.get("topic", "affiliate promotion"),
            caption=prepared["content"],
            source="affiliate_action",
            risk_level=i.risk_level,
            decision_id=i.decision_id,
            durable=False,
            execution_managed=True,
            image_url=p.get("image_url"),
            idempotency_key=i.idempotency_key,
        )
        return (
            result
            if isinstance(result, dict)
            else {
                "ok": False,
                "outcome": "failed",
                "error": "publish returned invalid result",
            }
        )

    async def affiliate_pause(p, i):
        product_id = str(p["product_id"])
        result = (
            db.table("affiliate_products")
            .update({"status": "paused"})
            .eq("id", product_id)
            .eq("workspace_id", i.workspace_id)
            .execute()
        )
        if not getattr(result, "data", None):
            return {
                "ok": False,
                "outcome": "failed",
                "error": "affiliate product not found",
            }
        return {"ok": True, "status": "paused", "product_id": product_id}

    async def affiliate_optimize(p, i):
        service = AffiliateService(db)
        analytics = service.analytics(
            workspace_id=i.workspace_id, days=int(p.get("days", 30))
        )
        # Optimization is deliberately advisory: it produces evidence and a
        # recommendation. It cannot silently pause/change revenue-producing
        # assets.
        recommendation = (
            "increase_testing"
            if analytics["clicks"] and analytics["conversions"] == 0
            else "continue_monitoring"
        )
        return {
            "ok": True,
            "recommendation": recommendation,
            "analytics": analytics,
        }

    kernel.register(
        ActionContract(
            "affiliate_create_product",
            risk_level="low",
            required_parameters=("name", "provider", "program", "destination_domains"),
            handler=affiliate_create_product,
        )
    )
    kernel.register(
        ActionContract(
            "affiliate_create_link",
            risk_level="low",
            required_parameters=("product_id", "destination_url"),
            handler=affiliate_create_link,
        )
    )
    kernel.register(
        ActionContract(
            "affiliate_publish_promotion",
            risk_level="high",
            required_parameters=("platform", "caption"),
            requires_approval=True,
            handler=affiliate_publish,
        )
    )
    kernel.register(
        ActionContract(
            "affiliate_pause_product",
            risk_level="high",
            required_parameters=("product_id",),
            requires_approval=True,
            handler=affiliate_pause,
        )
    )
    kernel.register(
        ActionContract(
            "affiliate_optimize_campaign",
            risk_level="medium",
            required_parameters=(),
            handler=affiliate_optimize,
        )
    )

    # ── Content operating controls ─────────────────────────────────────
    async def content_adjust_mix(p, i):
        from services.content_engine import content_engine

        brand_id = str(p.get("brand_id") or "").strip()
        factor = float(p.get("factor", 0.0))
        duration_posts = int(p.get("duration_posts", 5))
        if not brand_id:
            return {
                "ok": False,
                "outcome": "failed",
                "error": "content_adjust_mix requires brand_id",
            }
        if factor > 0:
            result = await content_engine.boost_content(
                brand_id, factor=factor, duration_posts=duration_posts
            )
        else:
            result = await content_engine.reduce_content(
                brand_id, factor=abs(factor), duration_posts=duration_posts
            )
        return {"ok": True, "outcome": "succeeded", "result": result}

    kernel.register(
        ActionContract(
            "content_adjust_mix",
            risk_level="medium",
            required_parameters=("brand_id", "factor"),
            autonomous_retryable=True,
            handler=content_adjust_mix,
        )
    )

    # ── Publishing domain adapter ─────────────────────────────────────
    # Publishing-specific parameter translation lives in the domain layer.
    # The Action Kernel owns only governance, identity, and execution semantics.
    from domain.publishing.action_handler import publish_content

    kernel.register(
        ActionContract(
            "publish_content",
            risk_level="high",
            required_parameters=("platform", "caption", "decision_id"),
            requires_approval=True,
            inline_with_decision=True,
            handler=publish_content,
        )
    )

    # ── Website publication ────────────────────────────────────────────
    async def website_publish(p, i):
        from repositories.social_account_repository import SocialAccountRepository
        from services.connectors.connector_factory import create_connector

        account_id = str(p.get("social_account_id") or "").strip()
        if not account_id:
            return {
                "ok": False,
                "outcome": "failed",
                "error": "website_publish requires social_account_id",
            }
        account = SocialAccountRepository(db).find_by_id(account_id)
        if account is None or str(account.workspace_id) != i.workspace_id:
            return {
                "ok": False,
                "outcome": "failed",
                "error": "Website account not found in workspace",
            }
        if not account.enabled or str(account.platform).lower() != "website":
            return {
                "ok": False,
                "outcome": "failed",
                "error": "selected account is not an enabled Website connector",
            }
        connector = create_connector(account)
        result = await asyncio.to_thread(
            connector.publish,
            str(p.get("content") or p.get("caption") or ""),
            image_url=p.get("image_url"),
            title=p.get("title") or p.get("topic") or "KDO publication",
            slug=p.get("slug") or "",
            idempotency_key=i.idempotency_key,
        )
        if not getattr(result, "ok", False):
            return {
                "ok": False,
                "outcome": getattr(result, "outcome", "failed") or "failed",
                "error": getattr(result, "error", None)
                or "Website publication failed",
            }
        return {
            "ok": True,
            "outcome": "succeeded",
            "provider_object_id": getattr(result, "post_id", ""),
            "platform": "website",
            "metadata": getattr(result, "metadata", {}),
        }

    kernel.register(
        ActionContract(
            "website_publish",
            risk_level="high",
            required_parameters=("social_account_id", "content", "decision_id"),
            requires_approval=True,
            inline_with_decision=True,
            handler=website_publish,
        )
    )

    return kernel
