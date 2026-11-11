"""Durable, governed internal business automation.

Rules are data, not executable code. Persistence is Supabase-backed when available;
production refuses to silently downgrade to process memory. The Condition Engine
remains the decision authority and ExternalActionExecutor remains the side-effect
boundary for consequential actions.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import logging
import os
import time
import uuid
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("kanyoza.business_automation")
OPS = {"eq", "ne", "gt", "gte", "lt", "lte", "contains", "in", "not_in", "exists", "truthy"}
RISKS = {"low", "medium", "high", "critical"}

@dataclass
class AutomationRule:
    id: str
    workspace_id: str
    name: str
    enabled: bool = True
    condition: Dict[str, Any] = field(default_factory=dict)
    action: str = "log"
    parameters: Dict[str, Any] = field(default_factory=dict)
    risk: str = "low"
    schedule_at: Optional[str] = None
    interval_seconds: Optional[int] = None
    next_run_at: Optional[str] = None
    version: int = 1
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

@dataclass
class AutomationExecution:
    id: str
    rule_id: str
    workspace_id: str
    action: str
    status: str
    dry_run: bool
    started_at: float
    finished_at: float
    output: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    idempotency_key: Optional[str] = None
    attempt: int = 0

class BusinessAutomationEngine:
    # Explicitly read-only capabilities are not provider side effects and do
    # not require a second Action Kernel execution contract.
    READ_ONLY_ACTIONS = frozenset({"generate_workspace_report"})

    VERSION = "2.0.0"

    def __init__(self, supabase_client=None):
        self._db = supabase_client
        self._rules: Dict[str, AutomationRule] = {}
        self._history: List[AutomationExecution] = []
        self._actions: Dict[str, Callable[..., Any]] = {}
        self._lock = asyncio.Lock()
        self.register_action("log", self._action_log)
        self.register_action("set_context", self._action_set_context)
        self.register_action("content_intelligence", self._action_content_intelligence)
        self.register_action("condition_decision", self._action_condition_decision)
        # Governed internal business adapters. These call existing domain services;
        # they never accept arbitrary import paths, shell commands, or URLs.
        self.register_action("crm_add_contact", self._action_crm_add_contact)
        self.register_action("crm_add_deal", self._action_crm_add_deal)
        self.register_action("crm_schedule_followup", self._action_crm_schedule_followup)
        self.register_action("crm_update_contact_status", self._action_crm_update_contact_status)
        self.register_action("crm_update_deal_stage", self._action_crm_update_deal_stage)
        self.register_action("crm_add_meeting", self._action_crm_add_meeting)
        self.register_action("generate_workspace_report", self._action_generate_workspace_report)
        self.register_action("notify_operator", self._action_notify_operator)
        self.register_action("whatsapp_message", self._action_whatsapp_message)
        self.register_action("customer_communication", self._action_customer_communication)
        self.register_action("sales_orchestrate", self._action_sales_orchestrate)

    def set_supabase_client(self, client) -> None:
        self._db = client

    def _get_db(self):
        if self._db is not None:
            return self._db
        try:
            from services.supabase import supabase_service
            self._db = supabase_service.service_client or supabase_service.client
        except Exception:
            self._db = None
        return self._db

    @staticmethod
    def _production() -> bool:
        try:
            from config.settings import settings
            # Unit tests and local library use may construct the service without a
            # runtime mode. Production application modes are fail-closed.
            mode = os.getenv("SERVICE_MODE", "").strip().lower()
            return bool(getattr(settings, "is_production", False)) and mode in {"full", "api", "core", "director", "meta", "condition", "workers", "monitor", "github", "chaos"}
        except Exception:
            return False

    def register_action(self, name: str, handler: Callable[..., Any]) -> None:
        if not name or not callable(handler):
            raise ValueError("valid action name and callable handler required")
        self._actions[name] = handler

    @staticmethod
    def _row_to_rule(row: dict[str, Any]) -> AutomationRule:
        return AutomationRule(
            id=str(row.get("rule_id") or row.get("id")), workspace_id=str(row["workspace_id"]),
            name=str(row.get("name") or row.get("action") or "automation"), enabled=bool(row.get("enabled", True)),
            condition=row.get("condition") or {}, action=str(row.get("action") or "log"),
            parameters=row.get("parameters") or {}, risk=str(row.get("risk") or "low"),
            schedule_at=row.get("schedule_at"), interval_seconds=row.get("interval_seconds"),
            next_run_at=row.get("next_run_at"), version=int(row.get("version") or 1),
            created_at=row.get("created_at"), updated_at=row.get("updated_at"),
        )

    async def create_rule(self, workspace_id: str, payload: Dict[str, Any]) -> AutomationRule:
        ws = str(workspace_id or "").strip()
        if not ws: raise ValueError("workspace_id is required")
        action = str(payload.get("action") or "").strip()
        if action not in self._actions: raise ValueError(f"unsupported action: {action}")
        condition = payload.get("condition") or {}
        self._validate_condition(condition)
        risk = str(payload.get("risk") or "low").lower()
        if risk not in RISKS: raise ValueError("invalid risk")
        interval = payload.get("interval_seconds")
        if interval is not None:
            interval = int(interval)
            if interval < 60 or interval > 2_592_000: raise ValueError("interval_seconds must be 60..2592000")
        schedule_at = payload.get("schedule_at")
        rule = AutomationRule(
            id=f"rule_{uuid.uuid4().hex[:16]}", workspace_id=ws,
            name=str(payload.get("name") or action)[:120], enabled=bool(payload.get("enabled", True)),
            condition=copy.deepcopy(condition), action=action,
            parameters=copy.deepcopy(payload.get("parameters") or {}), risk=risk,
            schedule_at=schedule_at, interval_seconds=interval,
            next_run_at=schedule_at, version=1,
        )
        db = self._get_db()
        if db is not None:
            row = {"rule_id": rule.id, "workspace_id": ws, "name": rule.name, "enabled": rule.enabled,
                   "condition": rule.condition, "action": rule.action, "parameters": rule.parameters,
                   "risk": rule.risk, "schedule_at": rule.schedule_at, "interval_seconds": rule.interval_seconds,
                   "next_run_at": rule.next_run_at, "version": 1}
            result = await asyncio.to_thread(lambda: db.table("business_automation_rules").insert(row).execute())
            if not result.data: raise RuntimeError("durable automation rule insert returned no data")
            return self._row_to_rule(result.data[0])
        if self._production(): raise RuntimeError("durable business automation database is required in production")
        async with self._lock: self._rules[rule.id] = copy.deepcopy(rule)
        return rule

    async def update_rule(self, workspace_id: str, rule_id: str, payload: Dict[str, Any]) -> AutomationRule:
        ws = str(workspace_id or "").strip()
        rid = str(rule_id or "").strip()
        if not ws or not rid:
            raise ValueError("workspace_id and rule_id are required")
        db = self._get_db()
        if db is not None:
            result = await asyncio.to_thread(lambda: db.table("business_automation_rules").select("*").eq("workspace_id", ws).eq("rule_id", rid).limit(1).execute())
            if not result.data:
                raise KeyError("automation rule not found")
            current = self._row_to_rule(result.data[0])
        else:
            if self._production():
                raise RuntimeError("durable business automation database is required in production")
            async with self._lock:
                current = copy.deepcopy(self._rules.get(rid))
            if current is None or current.workspace_id != ws:
                raise KeyError("automation rule not found")

        allowed = {"name", "enabled", "condition", "action", "parameters", "risk", "schedule_at", "interval_seconds"}
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError(f"unsupported rule fields: {', '.join(sorted(unknown))}")

        updated = copy.deepcopy(current)
        if "name" in payload:
            updated.name = str(payload.get("name") or updated.name).strip()[:120]
            if not updated.name:
                raise ValueError("name is required")
        if "enabled" in payload:
            updated.enabled = bool(payload.get("enabled"))
        if "condition" in payload:
            condition = payload.get("condition") or {}
            self._validate_condition(condition)
            updated.condition = copy.deepcopy(condition)
        if "action" in payload:
            action = str(payload.get("action") or "").strip()
            if action not in self._actions:
                raise ValueError(f"unsupported action: {action}")
            updated.action = action
        if "parameters" in payload:
            parameters = payload.get("parameters") or {}
            if not isinstance(parameters, dict):
                raise ValueError("parameters must be an object")
            updated.parameters = copy.deepcopy(parameters)
        if "risk" in payload:
            risk = str(payload.get("risk") or "low").lower()
            if risk not in RISKS:
                raise ValueError("invalid risk")
            updated.risk = risk
        if "interval_seconds" in payload:
            interval = payload.get("interval_seconds")
            if interval is not None:
                interval = int(interval)
                if interval < 60 or interval > 2_592_000:
                    raise ValueError("interval_seconds must be 60..2592000")
            updated.interval_seconds = interval
        if "schedule_at" in payload:
            updated.schedule_at = payload.get("schedule_at")
            updated.next_run_at = updated.schedule_at
        if "enabled" in payload and not updated.enabled:
            updated.next_run_at = None
        elif "enabled" in payload and updated.enabled and updated.next_run_at is None:
            updated.next_run_at = updated.schedule_at

        updated.version = current.version + 1
        updated.updated_at = datetime.now(timezone.utc).isoformat()

        if db is not None:
            row = {"name": updated.name, "enabled": updated.enabled, "condition": updated.condition,
                   "action": updated.action, "parameters": updated.parameters, "risk": updated.risk,
                   "schedule_at": updated.schedule_at, "interval_seconds": updated.interval_seconds,
                   "next_run_at": updated.next_run_at, "version": updated.version,
                   "updated_at": updated.updated_at}
            result = await asyncio.to_thread(lambda: db.table("business_automation_rules").update(row).eq("workspace_id", ws).eq("rule_id", rid).eq("version", current.version).select("*").execute())
            if not result.data:
                raise RuntimeError("automation rule changed concurrently; refresh and retry")
            return self._row_to_rule(result.data[0])

        async with self._lock:
            self._rules[rid] = copy.deepcopy(updated)
            return copy.deepcopy(updated)

    async def list_rules(self, workspace_id: str) -> List[AutomationRule]:
        db = self._get_db()
        if db is not None:
            result = await asyncio.to_thread(lambda: db.table("business_automation_rules").select("*").eq("workspace_id", workspace_id).order("created_at", desc=True).execute())
            return [self._row_to_rule(r) for r in (result.data or [])]
        if self._production(): raise RuntimeError("durable business automation database is required in production")
        async with self._lock: return [copy.deepcopy(r) for r in self._rules.values() if r.workspace_id == workspace_id]

    _TEMPLATE_RE = re.compile(r"\{\{\s*event\.([a-zA-Z0-9_.-]+)\s*\}\}")

    @classmethod
    def _render_parameter_value(cls, value: Any, event: Dict[str, Any]) -> Any:
        """Resolve bounded event references in declarative rule parameters.

        Rules remain data-only: interpolation can read trusted event fields but
        cannot execute expressions, call functions, import code, or dereference
        arbitrary objects. Exact placeholders preserve the source value type.
        """
        if isinstance(value, dict):
            return {k: cls._render_parameter_value(v, event) for k, v in value.items()}
        if isinstance(value, list):
            return [cls._render_parameter_value(v, event) for v in value]
        if not isinstance(value, str):
            return value
        match = cls._TEMPLATE_RE.fullmatch(value.strip())
        if match:
            current: Any = event
            for part in match.group(1).split('.'):
                if not isinstance(current, dict) or part not in current:
                    return ""
                current = current[part]
            return current
        return cls._TEMPLATE_RE.sub(
            lambda m: str(cls._render_parameter_value(m.group(0), event)), value
        )

    @classmethod
    def _render_parameters(cls, parameters: Dict[str, Any], event: Dict[str, Any]) -> Dict[str, Any]:
        return cls._render_parameter_value(parameters or {}, event)

    @staticmethod
    def _event_trigger_matches(rule: AutomationRule, event_name: str) -> bool:
        """Return True only for explicitly event-triggered rules.

        Scheduled/manual rules remain unchanged. Event-driven automation is opt-in
        through parameters.trigger_events, preventing a newly published event from
        unexpectedly activating legacy rules.
        """
        triggers = rule.parameters.get("trigger_events") if isinstance(rule.parameters, dict) else None
        if not triggers:
            return False
        if isinstance(triggers, str):
            triggers = [triggers]
        if not isinstance(triggers, list):
            return False
        normalized = {str(x).strip() for x in triggers if str(x).strip()}
        return event_name in normalized or "*" in normalized

    async def run_event(self, *, event_name: str, event: Dict[str, Any], workspace_id: str,
                        approved: bool = False) -> List[AutomationExecution]:
        """Execute only rules explicitly subscribed to an event name.

        Event metadata is trusted from the EventBus; workspace identity is never
        accepted from a rule parameter as authority. Consequential actions still
        pass through their existing governance/execution boundaries.
        """
        ws = str(workspace_id or "").strip()
        name = str(event_name or "").strip()
        if not ws or not name:
            return []
        rules = await self.list_rules(ws)
        eligible = [r for r in rules if r.enabled and self._event_trigger_matches(r, name)]
        enriched = copy.deepcopy(event or {})
        enriched["event_name"] = name
        event_id = str(enriched.get("event_id") or "").strip()
        # Event-driven automation requires a stable producer identity. Without it,
        # the execution idempotency fence collapses distinct events onto the same
        # ``missing`` key and can silently suppress a legitimate business action.
        if not event_id:
            raise ValueError("event-driven automation requires event_id")
        enriched["event_id"] = event_id
        enriched["workspace_id"] = ws
        return [await self._execute_rule(
            r, ws, enriched, dry_run=False, approved=approved,
            idempotency_key=f"event:{r.id}:{name}:{enriched.get('event_id') or 'missing'}:{r.version}"
        ) for r in eligible if self.evaluate(r.condition, enriched)]

    async def run(self, workspace_id: str, *, event: Dict[str, Any], rule_id: Optional[str] = None,
                  dry_run: bool = True, approved: bool = False, idempotency_key: Optional[str] = None) -> List[AutomationExecution]:
        rules = await self.list_rules(workspace_id)
        if rule_id: rules = [r for r in rules if r.id == rule_id]
        return [await self._execute_rule(r, workspace_id, event, dry_run=dry_run, approved=approved, idempotency_key=idempotency_key) for r in rules if r.enabled and self.evaluate(r.condition, event)]

    async def run_due(self, *, limit: int = 20) -> List[AutomationExecution]:
        db = self._get_db()
        if db is None:
            if self._production(): raise RuntimeError("durable business automation database is required in production")
            return []
        result = await asyncio.to_thread(lambda: db.rpc("claim_due_business_automation_rules", {"p_limit": int(max(1, min(limit, 100)))}).execute())
        executions = []
        for row in result.data or []:
            rule = self._row_to_rule(row)
            try:
                scheduled_event = copy.deepcopy(rule.parameters.get("event") or {})
                scheduled_event.setdefault("event_id", f"schedule:{rule.id}:{rule.next_run_at or rule.schedule_at}")
                executions.extend(await self.run(rule.workspace_id, event=scheduled_event, rule_id=rule.id, dry_run=False))
                await self._mark_due_complete(rule)
            except Exception:
                logger.exception("due automation execution failed rule=%s", rule.id)
        return executions

    async def _mark_due_complete(self, rule: AutomationRule) -> None:
        db = self._get_db()
        if db is None: return
        if rule.interval_seconds:
            from datetime import timedelta
            nxt = (datetime.now(timezone.utc) + timedelta(seconds=rule.interval_seconds)).isoformat()
        else: nxt = None
        await asyncio.to_thread(lambda: db.table("business_automation_rules").update({"next_run_at": nxt, "updated_at": datetime.now(timezone.utc).isoformat()}).eq("rule_id", rule.id).eq("version", rule.version).execute())

    async def _reserve_execution(self, *, rule: AutomationRule, workspace_id: str, action: str, key: str, dry_run: bool) -> Optional[AutomationExecution]:
        """Reserve an execution before any action runs; durable uniqueness is the replay fence."""
        db = self._get_db()
        if db is None:
            return None
        execution_id = "exec_" + hashlib.sha256(f"{workspace_id}|{key}".encode()).hexdigest()[:24]
        payload = {"execution_id": execution_id, "rule_id": rule.id, "workspace_id": workspace_id,
                   "action": action, "status": "started", "dry_run": dry_run,
                   "started_at": datetime.now(timezone.utc).isoformat(),
                   "finished_at": datetime.now(timezone.utc).isoformat(), "output": {},
                   "idempotency_key": key, "attempt": 0}
        result = await asyncio.to_thread(lambda: db.rpc("reserve_business_automation_execution", {"p_execution": payload}).execute())
        row = (result.data or [None])[0] if isinstance(result.data, list) else result.data
        if not row:
            raise RuntimeError("business automation execution reservation returned no authoritative row")
        # The database RPC marks a newly acquired reservation explicitly.
        # Existing completed/failed/active reservations are returned as replay
        # evidence and must not be executed a second time.
        marker = (row.get("output") or {}).get("_reservation_acquired") if isinstance(row, dict) else None
        if str(row.get("status")) == "started" and marker is True:
            return None
        return AutomationExecution(id=row["execution_id"], rule_id=row["rule_id"], workspace_id=row["workspace_id"],
            action=row["action"], status=row["status"], dry_run=bool(row.get("dry_run")), started_at=0, finished_at=0,
            output={k: v for k, v in (row.get("output") or {}).items() if k != "_reservation_acquired"},
            error=row.get("error"), idempotency_key=row.get("idempotency_key"), attempt=int(row.get("attempt") or 0))

    async def _execute_rule(self, rule: AutomationRule, workspace_id: str, event: Dict[str, Any], *, dry_run: bool, approved: bool, idempotency_key: Optional[str]) -> AutomationExecution:
        start = time.time()
        key = idempotency_key or f"automation:{rule.id}:{event.get('event_id') or uuid.uuid4().hex}"
        existing = await self._reserve_execution(rule=rule, workspace_id=workspace_id, action=rule.action, key=key, dry_run=dry_run)
        if existing is not None:
            existing.finished_at = time.time()
            return existing
        execution_id = "exec_" + hashlib.sha256(f"{workspace_id}|{key}".encode()).hexdigest()[:24] if self._get_db() is not None else f"exec_{uuid.uuid4().hex[:16]}"
        execution = AutomationExecution(execution_id, rule.id, workspace_id, rule.action,
                                         "dry_run" if dry_run else "started", dry_run, start, start, idempotency_key=key)
        try:
            if rule.risk in {"high", "critical"} and not approved:
                execution.status, execution.output = "approval_required", {"reason": "high-impact action requires approval"}
            elif dry_run:
                execution.output = {"would_execute": rule.action, "parameters": copy.deepcopy(rule.parameters)}
            else:
                rendered_parameters = self._render_parameters(rule.parameters, event)
                value = self._actions[rule.action](event, rendered_parameters, workspace_id=workspace_id, rule=rule, idempotency_key=key, approved=approved)
                if asyncio.iscoroutine(value): value = await value
                execution.output = value if isinstance(value, dict) else {"result": value}
                result_status = str(execution.output.get("status") or "").lower() if isinstance(execution.output, dict) else ""
                if result_status in {"uncertain", "reconciliation_required"}:
                    execution.status = "uncertain"
                elif result_status in {"failed", "approval_required", "in_progress"} or execution.output.get("ok") is False:
                    execution.status = result_status if result_status in {"failed", "approval_required", "in_progress"} else "failed"
                else:
                    execution.status = "completed"
        except Exception as exc:
            execution.status, execution.error = "failed", str(exc)[:500]
        execution.finished_at = time.time()
        await self._persist_execution(execution)
        return copy.deepcopy(execution)

    async def _persist_execution(self, execution: AutomationExecution) -> None:
        db = self._get_db()
        if db is not None:
            row = {"execution_id": execution.id, "rule_id": execution.rule_id, "workspace_id": execution.workspace_id,
                   "action": execution.action, "status": execution.status, "dry_run": execution.dry_run,
                   "started_at": datetime.fromtimestamp(execution.started_at, timezone.utc).isoformat(),
                   "finished_at": datetime.fromtimestamp(execution.finished_at, timezone.utc).isoformat(),
                   "output": execution.output, "error": execution.error, "idempotency_key": execution.idempotency_key,
                   "attempt": execution.attempt}
            # Reservation already inserted the execution row. Finalization must
            # UPDATE it; inserting here would collide with the idempotency fence.
            result = await asyncio.to_thread(lambda: db.table("business_automation_executions").update({
                "status": row["status"], "dry_run": row["dry_run"], "finished_at": row["finished_at"],
                "output": row["output"], "error": row["error"], "attempt": row["attempt"]
            }).eq("execution_id", execution.id).eq("workspace_id", execution.workspace_id).eq("status", "started").select("execution_id").execute())
            if not result.data:
                raise RuntimeError(
                    "business automation finalization rejected: reservation is no longer started"
                )
            return
        if self._production(): raise RuntimeError("durable business automation database is required in production")
        async with self._lock:
            self._history.append(execution); self._history = self._history[-1000:]

    async def history(self, workspace_id: str, limit: int = 50) -> List[AutomationExecution]:
        db = self._get_db()
        if db is not None:
            result = await asyncio.to_thread(lambda: db.table("business_automation_executions").select("*").eq("workspace_id", workspace_id).order("started_at", desc=True).limit(max(1, min(limit, 200))).execute())
            return [AutomationExecution(id=r["execution_id"], rule_id=r["rule_id"], workspace_id=r["workspace_id"], action=r["action"], status=r["status"], dry_run=bool(r.get("dry_run")), started_at=0, finished_at=0, output=r.get("output") or {}, error=r.get("error"), idempotency_key=r.get("idempotency_key"), attempt=int(r.get("attempt") or 0)) for r in (result.data or [])]
        if self._production(): raise RuntimeError("durable business automation database is required in production")
        async with self._lock: return [copy.deepcopy(x) for x in self._history if x.workspace_id == workspace_id][-max(1, min(limit, 200)):]

    @staticmethod
    def evaluate(condition: Dict[str, Any], event: Dict[str, Any]) -> bool:
        if not condition: return True
        if "all" in condition: return all(BusinessAutomationEngine.evaluate(c, event) for c in condition["all"])
        if "any" in condition: return any(BusinessAutomationEngine.evaluate(c, event) for c in condition["any"])
        field, op = str(condition.get("field") or ""), str(condition.get("op") or "eq")
        if op not in OPS or not field: return False
        value, target = BusinessAutomationEngine._event_value(event, field), condition.get("value")
        try:
            if op == "eq": return value == target
            if op == "ne": return value != target
            if op == "gt": return value > target
            if op == "gte": return value >= target
            if op == "lt": return value < target
            if op == "lte": return value <= target
            if op == "contains": return target in value
            if op == "in": return value in target
            if op == "not_in": return value not in target
            if op == "exists":
                exists = BusinessAutomationEngine._event_path_exists(event, field)
                return exists == bool(target if target is not None else True)
            if op == "truthy": return bool(value) == bool(target if target is not None else True)
        except (TypeError, ValueError): return False
        return False

    @staticmethod
    def _event_path_exists(event: Dict[str, Any], field: str) -> bool:
        """Return whether a bounded dotted event path is actually present."""
        current: Any = event
        for part in str(field or "").split("."):
            if not isinstance(current, dict) or part not in current:
                return False
            current = current[part]
        return True

    @staticmethod
    def _event_value(event: Dict[str, Any], field: str) -> Any:
        """Resolve a bounded dotted path from event payloads.

        This permits governed projections such as
        ``business_decision_input.intent`` without allowing expression
        evaluation, object traversal, or executable lookups.
        """
        current: Any = event
        for part in str(field or "").split("."):
            if not isinstance(current, dict) or part not in current:
                return None
            current = current[part]
        return current

    @staticmethod
    def _validate_condition(condition: Dict[str, Any]) -> None:
        if not isinstance(condition, dict): raise ValueError("condition must be an object")
        if not condition:
            return
        if "all" in condition or "any" in condition:
            key = "all" if "all" in condition else "any"
            if not isinstance(condition[key], list) or len(condition[key]) > 20: raise ValueError("condition groups must be lists of <=20")
            for child in condition[key]: BusinessAutomationEngine._validate_condition(child)
            return
        if str(condition.get("op") or "eq") not in OPS or not condition.get("field"): raise ValueError("condition requires field and supported op")


    @staticmethod
    def _crm_plugin(workspace_id: str):
        """Resolve the existing CRM plugin without exposing its repository as an action API."""
        from plugins.industries.crm.plugin import CRMPlugin
        try:
            from services.supabase import supabase_service
            db = supabase_service.service_client or supabase_service.client
        except Exception:
            db = None
        plugin = CRMPlugin(db)
        return plugin, str(workspace_id)

    @staticmethod
    def _require_action_success(result: Any, action: str) -> dict:
        """Normalize domain-plugin results; never mark an ok=False result completed."""
        if not isinstance(result, dict):
            raise RuntimeError(f"{action} returned a non-object result")
        if result.get("ok") is False:
            raise RuntimeError(str(result.get("error") or f"{action} failed")[:300])
        return result

    async def _execute_governed_crm(self, action: str, event, parameters, **kwargs):
        from services.action_kernel import build_default_action_kernel
        ws = str(kwargs.get("workspace_id") or "").strip()
        if not ws:
            raise ValueError(f"{action} requires workspace scope")
        payload = {**event, **parameters}
        plugin, _ = self._crm_plugin(ws)
        kernel = build_default_action_kernel(self._get_db(), allow_in_memory=not self._production(), plugin=plugin)
        result = await kernel.execute(
            workspace_id=ws, action_type=action, parameters=payload,
            risk_level={"crm_add_contact":"medium", "crm_add_deal":"high",
                        "crm_schedule_followup":"medium", "crm_add_meeting":"medium",
                        "crm_update_contact_status":"medium", "crm_update_deal_stage":"medium"}[action],
            decision_id=getattr(kwargs.get("rule"), "id", None),
            correlation_id=str(event.get("correlation_id") or event.get("event_id") or ""),
            idempotency_key=str(kwargs.get("idempotency_key") or ""),
            source="business_automation",
        )
        if result.get("status") == "approval_required":
            return result
        if result.get("status") not in {"succeeded", "uncertain"}:
            raise RuntimeError(str(result.get("error") or (result.get("output") or {}).get("error") or f"{action} failed"))
        return result

    async def _action_crm_add_contact(self, event, parameters, **kwargs):
        payload = {**event, **parameters}
        if not str(payload.get("name") or payload.get("contact_name") or "").strip():
            raise ValueError("crm_add_contact requires name")
        result = await self._execute_governed_crm("crm_add_contact", event, parameters, **kwargs)
        if result.get("status") in {"approval_required", "uncertain"}:
            return result
        # Publish a bounded lifecycle event only after the governed CRM write
        # succeeds. The event contains no credentials and is tenant-scoped.
        try:
            from datetime import timedelta
            from core.unified_bus import get_unified_bus
            contact_data = ((result.get("output") or {}).get("data") or {}) if isinstance(result, dict) else {}
            contact_id = str(contact_data.get("contact_id") or "").strip()
            if contact_id:
                hours = float(parameters.get("auto_followup_hours", 24) or 24)
                if hours < 1 or hours > 720:
                    raise ValueError("auto_followup_hours must be 1..720")
                follow_up_at = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
                bus = await get_unified_bus()
                published = await bus.publish(
                    "crm.contact.created",
                    {
                        "contact_id": contact_id,
                        "name": str(payload.get("name") or payload.get("contact_name") or ""),
                        "phone": str(payload.get("phone") or ""),
                        "email": str(payload.get("email") or ""),
                        "company": str(payload.get("company") or ""),
                        "follow_up_at": follow_up_at,
                        "parent_event_id": str(event.get("event_id") or ""),
                    },
                    source="business_automation.lifecycle",
                    correlation_id=str(event.get("correlation_id") or event.get("event_id") or ""),
                    parent_event_id=str(event.get("event_id") or "") or None,
                    workspace_id=str(kwargs.get("workspace_id") or ""),
                    strict_nerve=True,
                )
                if not published:
                    return {**result, "status": "uncertain", "reconciliation_required": True,
                            "lifecycle_event": {"event_name": "crm.contact.created", "status": "publish_failed"}}
                result = {**result, "lifecycle_event": {"event_name": "crm.contact.created", "event_id": published}}
        except Exception as exc:
            logger.exception("CRM contact lifecycle event failed")
            return {**result, "status": "uncertain", "reconciliation_required": True,
                    "lifecycle_event": {"event_name": "crm.contact.created", "status": "failed", "error": str(exc)[:300]}}
        return result

    async def _action_crm_add_deal(self, event, parameters, **kwargs):
        payload = {**event, **parameters}
        if not payload.get("amount") or not payload.get("contact"):
            raise ValueError("crm_add_deal requires amount and contact")
        return await self._execute_governed_crm("crm_add_deal", event, parameters, **kwargs)

    async def _action_crm_schedule_followup(self, event, parameters, **kwargs):
        payload = {**event, **parameters}
        if not payload.get("contact") or not payload.get("date"):
            raise ValueError("crm_schedule_followup requires contact and date")
        return await self._execute_governed_crm("crm_schedule_followup", event, parameters, **kwargs)

    async def _action_crm_update_contact_status(self, event, parameters, **kwargs):
        payload = {**event, **parameters}
        if not payload.get("contact_id") or not payload.get("status"):
            raise ValueError("crm_update_contact_status requires contact_id and status")
        result = await self._execute_governed_crm("crm_update_contact_status", event, parameters, **kwargs)
        if result.get("status") in {"approval_required", "uncertain"}:
            return result
        data = (result.get("output") or {}).get("data") or {}
        try:
            from core.unified_bus import get_unified_bus
            bus = await get_unified_bus()
            published = await bus.publish("crm.contact.status_changed", {**data, "previous_status": event.get("previous_status"), "parent_event_id": event.get("event_id")}, source="business_automation.lifecycle", correlation_id=str(event.get("correlation_id") or event.get("event_id") or ""), parent_event_id=str(event.get("event_id") or "") or None, workspace_id=str(kwargs.get("workspace_id") or ""), strict_nerve=True)
            if not published:
                return {**result, "status": "uncertain", "reconciliation_required": True}
            return {**result, "lifecycle_event": {"event_name": "crm.contact.status_changed", "event_id": published}}
        except Exception as exc:
            return {**result, "status": "uncertain", "reconciliation_required": True, "error": str(exc)[:300]}

    async def _action_crm_update_deal_stage(self, event, parameters, **kwargs):
        payload = {**event, **parameters}
        if not payload.get("deal_id") or not payload.get("stage"):
            raise ValueError("crm_update_deal_stage requires deal_id and stage")
        result = await self._execute_governed_crm("crm_update_deal_stage", event, parameters, **kwargs)
        if result.get("status") in {"approval_required", "uncertain"}:
            return result
        data = (result.get("output") or {}).get("data") or {}
        try:
            from core.unified_bus import get_unified_bus
            bus = await get_unified_bus()
            published = await bus.publish("crm.deal.stage_changed", {**data, "previous_stage": event.get("previous_stage"), "parent_event_id": event.get("event_id")}, source="business_automation.lifecycle", correlation_id=str(event.get("correlation_id") or event.get("event_id") or ""), parent_event_id=str(event.get("event_id") or "") or None, workspace_id=str(kwargs.get("workspace_id") or ""), strict_nerve=True)
            if not published:
                return {**result, "status": "uncertain", "reconciliation_required": True}
            return {**result, "lifecycle_event": {"event_name": "crm.deal.stage_changed", "event_id": published}}
        except Exception as exc:
            return {**result, "status": "uncertain", "reconciliation_required": True, "error": str(exc)[:300]}

    async def _action_crm_add_meeting(self, event, parameters, **kwargs):
        payload = {**event, **parameters}
        if not str(payload.get("title") or "").strip():
            raise ValueError("crm_add_meeting requires title")
        return await self._execute_governed_crm("crm_add_meeting", event, parameters, **kwargs)


    async def _action_generate_workspace_report(self, event, parameters, **kwargs):
        ws = str(kwargs.get("workspace_id") or "").strip()
        if not ws:
            raise ValueError("generate_workspace_report requires workspace scope")
        days = int(parameters.get("window_days", event.get("window_days", 7)))
        if days < 1 or days > 90:
            raise ValueError("window_days must be 1..90")
        from core.organism_report import build_organism_report
        return build_organism_report(ws, window_days=days)

    async def _action_sales_orchestrate(self, event, parameters, **kwargs):
        """Choose one bounded next sales action, then delegate to Action Kernel."""
        from services.sales_orchestration import SalesOrchestrator
        ws = str(kwargs.get("workspace_id") or "").strip()
        if not ws:
            raise ValueError("sales_orchestrate requires workspace scope")
        key = str(kwargs.get("idempotency_key") or "").strip()
        if not key:
            raise ValueError("sales_orchestrate requires an automation idempotency key")
        return await SalesOrchestrator.execute(
            event={**event, **parameters},
            workspace_id=ws,
            idempotency_key=key,
            db=self._get_db(),
            production=self._production(),
            rule=kwargs.get("rule"),
        )

    async def _action_whatsapp_message(self, event, parameters, **kwargs):
        payload = {**event, **parameters}
        if not payload.get("social_account_id") or not payload.get("to_number") or not payload.get("message"):
            raise ValueError("whatsapp_message requires social_account_id, to_number and message")
        from services.action_kernel import build_default_action_kernel
        ws = str(kwargs.get("workspace_id") or "").strip()
        if not ws:
            raise ValueError("whatsapp_message requires workspace scope")
        kernel = build_default_action_kernel(self._get_db(), allow_in_memory=not self._production())
        result = await kernel.execute(
            workspace_id=ws, action_type="whatsapp_message", parameters=payload,
            risk_level="high", decision_id=getattr(kwargs.get("rule"), "id", None),
            correlation_id=str(event.get("correlation_id") or event.get("event_id") or ""),
            idempotency_key=str(kwargs.get("idempotency_key") or ""), source="business_automation",
        )
        return result

    async def _action_customer_communication(self, event, parameters, **kwargs):
        """Delegate normalized customer communication to the Action Kernel."""
        from services.action_kernel import build_default_action_kernel
        ws = str(kwargs.get("workspace_id") or "").strip()
        if not ws:
            raise ValueError("customer_communication requires workspace scope")
        payload = {**event, **parameters}
        channel = str(payload.get("channel") or "").strip().lower()
        recipient = str(payload.get("recipient") or payload.get("to_number") or "").strip()
        if channel == "whatsapp":
            payload["recipient"] = recipient
            payload["to_number"] = recipient
            if not payload.get("social_account_id"):
                raise ValueError("customer_communication WhatsApp requires social_account_id")
        elif channel == "email":
            payload["recipient"] = recipient
        else:
            raise ValueError("customer_communication supports email or whatsapp")
        kernel = build_default_action_kernel(self._get_db(), allow_in_memory=not self._production())
        return await kernel.execute(
            workspace_id=ws, action_type="customer_communication", parameters=payload,
            risk_level="high", decision_id=getattr(kwargs.get("rule"), "id", None),
            correlation_id=str(event.get("correlation_id") or event.get("event_id") or ""),
            idempotency_key=str(kwargs.get("idempotency_key") or ""), source="business_automation",
        )

    async def _action_notify_operator(self, event, parameters, **kwargs):
        """Delegate operator notification to the single governed Action Kernel."""
        from services.action_kernel import build_default_action_kernel
        ws = str(kwargs.get("workspace_id") or "").strip()
        execution_key = str(kwargs.get("idempotency_key") or "").strip()
        if not ws:
            raise ValueError("notify_operator requires workspace scope")
        if not execution_key:
            raise ValueError("notify_operator requires an automation idempotency key")
        payload = {**event, **parameters}
        if not str(payload.get("message") or "").strip():
            raise ValueError("notify_operator requires message")
        kernel = build_default_action_kernel(self._get_db(), allow_in_memory=not self._production())
        return await kernel.execute(
            workspace_id=ws, action_type="notify_operator", parameters=payload,
            risk_level="medium", decision_id=getattr(kwargs.get("rule"), "id", None),
            correlation_id=str(event.get("correlation_id") or event.get("event_id") or ""),
            idempotency_key=execution_key, source="business_automation",
        )

    @staticmethod
    def _action_log(event, parameters, **_):
        logger.info("Business automation event: %s", {"event": event, "parameters": parameters}); return {"logged": True}

    @staticmethod
    def _action_set_context(event, parameters, **_): return {"proposed_context": copy.deepcopy(parameters)}

    async def _action_content_intelligence(self, event, parameters, **kwargs):
        from services.content_intelligence import content_intelligence_engine
        topic = str(parameters.get("topic") or event.get("topic") or "").strip()
        if not topic: raise ValueError("content_intelligence action requires topic")
        result = await content_intelligence_engine.analyze(topic, context=str(parameters.get("context") or ""), workspace_id=kwargs.get("workspace_id", ""))
        return result.to_dict()

    async def _action_condition_decision(self, event, parameters, **kwargs):
        """Feed a business event into the audited Condition Engine lifecycle."""
        from services.condition_engine import Observation, autonomous_engine
        from datetime import datetime, timezone
        ws = str(kwargs.get("workspace_id") or parameters.get("workspace_id") or event.get("workspace_id") or "").strip()
        brand = str(parameters.get("brand_id") or event.get("brand_id") or "").strip()
        if not ws or not brand:
            raise ValueError("condition_decision requires workspace_id and brand_id")
        obs = Observation(
            post_id=str(event.get("post_id") or f"automation_{uuid.uuid4().hex[:12]}"), brand_id=brand,
            timestamp=datetime.now(timezone.utc),
            likes=float(event.get("likes", 0) or 0), comments=float(event.get("comments", 0) or 0),
            shares=float(event.get("shares", 0) or 0), clicks=float(event.get("clicks", 0) or 0),
            impressions=float(event.get("impressions", 0) or 0), reach=float(event.get("reach", 0) or 0),
            topic=str(event.get("topic") or parameters.get("topic") or ""),
            content_type=str(event.get("content_type") or ""), style=str(event.get("style") or ""),
            posting_hour=event.get("posting_hour"), has_image=bool(event.get("has_image", False)),
            has_video=bool(event.get("has_video", False)), has_question=bool(event.get("has_question", False)),
            has_cta=bool(event.get("has_cta", False)),
            metadata={"workspace_id": ws, "automation_rule": True, "event_id": event.get("event_id")},
        )
        observed = autonomous_engine.observe(obs)
        decision_id = str((observed.get("decision") or {}).get("id") or "") if isinstance(observed, dict) else ""
        if not decision_id:
            raise RuntimeError("condition_engine_returned_no_decision_id")
        decision = autonomous_engine.get_decision(decision_id)
        if decision is None:
            raise RuntimeError("condition_engine_decision_not_retrievable")
        plan = autonomous_engine.create_action_plan(decision)
        response = {"ok": True, "success": True, "observation": observed, "decision_id": decision_id, "plan_id": plan.id if plan else None}
        if plan and bool(parameters.get("execute", False)):
            # Execution stays inside the Condition Engine's Council/ledger/executor
            # boundary. Business Automation never calls an external provider itself.
            executed = await autonomous_engine.execute_plan_async(plan.id)
            response["execution"] = {
                "status": executed.status.value if hasattr(executed.status, "value") else str(executed.status),
                "outcome": executed.outcome, "action": executed.action, "confidence": executed.confidence,
            }
        return response

business_automation_engine = BusinessAutomationEngine()
