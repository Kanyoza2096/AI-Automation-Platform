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
        self.register_action("crm_add_meeting", self._action_crm_add_meeting)
        self.register_action("generate_workspace_report", self._action_generate_workspace_report)
        self.register_action("notify_operator", self._action_notify_operator)
        self.register_action("create_affiliate_post", self._action_create_affiliate_post)
        self.register_action("affiliate_optimize", self._action_affiliate_optimize)

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

    async def list_rules(self, workspace_id: str) -> List[AutomationRule]:
        db = self._get_db()
        if db is not None:
            result = await asyncio.to_thread(lambda: db.table("business_automation_rules").select("*").eq("workspace_id", workspace_id).order("created_at", desc=True).execute())
            return [self._row_to_rule(r) for r in (result.data or [])]
        if self._production(): raise RuntimeError("durable business automation database is required in production")
        async with self._lock: return [copy.deepcopy(r) for r in self._rules.values() if r.workspace_id == workspace_id]

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
        enriched["event_id"] = str(enriched.get("event_id") or "")
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
                executions.extend(await self.run(rule.workspace_id, event=scheduled_event, rule_id=rule.id, dry_run=False, approved=False))
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
                value = self._actions[rule.action](event, rule.parameters, workspace_id=workspace_id, rule=rule, idempotency_key=key, approved=approved)
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
            await asyncio.to_thread(lambda: db.table("business_automation_executions").update({
                "status": row["status"], "dry_run": row["dry_run"], "finished_at": row["finished_at"],
                "output": row["output"], "error": row["error"], "attempt": row["attempt"]
            }).eq("execution_id", execution.id).eq("workspace_id", execution.workspace_id).execute())
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
        value, target = event.get(field), condition.get("value")
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
            if op == "exists": return (field in event) == bool(target if target is not None else True)
            if op == "truthy": return bool(value) == bool(target if target is not None else True)
        except (TypeError, ValueError): return False
        return False

    @staticmethod
    def _validate_condition(condition: Dict[str, Any]) -> None:
        if not isinstance(condition, dict): raise ValueError("condition must be an object")
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
                        "crm_schedule_followup":"medium", "crm_add_meeting":"medium"}[action],
            decision_id=getattr(kwargs.get("rule"), "id", None),
            correlation_id=str(event.get("correlation_id") or event.get("event_id") or ""),
            idempotency_key=str(kwargs.get("idempotency_key") or ""),
            source="business_automation", approved=bool(kwargs.get("approved", False)),
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
        return await self._execute_governed_crm("crm_add_contact", event, parameters, **kwargs)

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

    async def _action_crm_add_meeting(self, event, parameters, **kwargs):
        payload = {**event, **parameters}
        if not str(payload.get("title") or "").strip():
            raise ValueError("crm_add_meeting requires title")
        return await self._execute_governed_crm("crm_add_meeting", event, parameters, **kwargs)


    async def _action_create_affiliate_post(self, event, parameters, **kwargs):
        """Create a governed affiliate publication intent; never publish inline."""
        from services.action_kernel import build_default_action_kernel
        ws = str(kwargs.get("workspace_id") or "").strip()
        if not ws:
            raise ValueError("create_affiliate_post requires workspace scope")
        kernel = build_default_action_kernel(self._get_db(), allow_in_memory=not self._production())
        payload = {**event, **parameters}
        return await kernel.enqueue(
            workspace_id=ws, action_type="affiliate_publish_promotion", parameters=payload,
            risk_level="high", decision_id=getattr(kwargs.get("rule"), "id", None),
            correlation_id=str(event.get("correlation_id") or event.get("event_id") or ""),
            idempotency_key=str(kwargs.get("idempotency_key") or ""), source="business_automation",
            approved=bool(kwargs.get("approved", False)),
        )

    async def _action_affiliate_optimize(self, event, parameters, **kwargs):
        from services.action_kernel import build_default_action_kernel
        ws = str(kwargs.get("workspace_id") or "").strip()
        if not ws:
            raise ValueError("affiliate_optimize requires workspace scope")
        kernel = build_default_action_kernel(self._get_db(), allow_in_memory=not self._production())
        return await kernel.execute(
            workspace_id=ws, action_type="affiliate_optimize_campaign", parameters={**event, **parameters},
            risk_level="medium", decision_id=getattr(kwargs.get("rule"), "id", None),
            correlation_id=str(event.get("correlation_id") or event.get("event_id") or ""),
            idempotency_key=str(kwargs.get("idempotency_key") or ""), source="business_automation",
            approved=bool(kwargs.get("approved", False)),
        )

    async def _action_generate_workspace_report(self, event, parameters, **kwargs):
        ws = str(kwargs.get("workspace_id") or "").strip()
        if not ws:
            raise ValueError("generate_workspace_report requires workspace scope")
        days = int(parameters.get("window_days", event.get("window_days", 7)))
        if days < 1 or days > 90:
            raise ValueError("window_days must be 1..90")
        from core.organism_report import build_organism_report
        return build_organism_report(ws, window_days=days)

    async def _action_notify_operator(self, event, parameters, **kwargs):
        message = str(parameters.get("message") or event.get("message") or "").strip()
        if not message:
            raise ValueError("notify_operator requires message")
        level = str(parameters.get("level") or "info").lower()
        if level not in {"info", "warning", "error", "critical"}:
            raise ValueError("invalid notification level")
        channels = parameters.get("channels") or ["slack"]
        allowed = {"slack", "discord", "email"}
        if not isinstance(channels, list) or not set(channels).issubset(allowed):
            raise ValueError("channels must be a subset of slack, discord, email")
        from services.notifier import notifier
        ws = str(kwargs.get("workspace_id") or "").strip()
        execution_key = str(kwargs.get("idempotency_key") or "").strip()
        if not ws:
            raise ValueError("notify_operator requires workspace scope")
        if not execution_key:
            raise ValueError("notify_operator requires an automation idempotency key")

        def deliver():
            results = notifier.alert(message, level=level, channels=channels)
            delivered = [name for name in channels if bool(results.get(name))]
            failed = [name for name in channels if not bool(results.get(name))]
            if delivered and failed:
                return {
                    "ok": False,
                    "delivery_status": "partial",
                    "delivered": delivered,
                    "failed": failed,
                    "level": level,
                    "reconciliation_required": True,
                }
            if not delivered:
                return {
                    "ok": False,
                    "delivery_status": "failed",
                    "delivered": [],
                    "failed": failed,
                    "level": level,
                }
            return {
                "ok": True,
                "delivery_status": "delivered",
                "delivered": delivered,
                "failed": [],
                "level": level,
            }

        from services.execution_ledger import ExecutionLedger
        from services.external_action_executor import ExternalActionExecutor
        ledger = ExecutionLedger(db=self._get_db(), allow_in_memory=not self._production())
        result = await ExternalActionExecutor(ledger).execute(
            workspace_id=ws,
            action="business_automation.notify_operator",
            idempotency_key=ExecutionLedger.make_idempotency_key("notification", ws, execution_key),
            provider="notification",
            operation=deliver,
            metadata={"channels": channels, "level": level, "automation_idempotency_key": execution_key},
            reconcile_on_exception=True,
        )
        if result.get("status") not in {"succeeded"}:
            result["reconciliation_required"] = result.get("status") == "uncertain" or result.get("reconciliation_required", False)
        return result

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
            executed = await autonomous_engine.execute_plan_async(plan.id, approved=bool(parameters.get("approved", False)))
            response["execution"] = {
                "status": executed.status.value if hasattr(executed.status, "value") else str(executed.status),
                "outcome": executed.outcome, "action": executed.action, "confidence": executed.confidence,
            }
        return response

business_automation_engine = BusinessAutomationEngine()
