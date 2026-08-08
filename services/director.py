"""
KANYOZA DIRECTOR v1.2 — The Autonomous CEO.

Single-file autonomous observation, decision, approval, execution,
learning, briefing, and notification engine.

v1.2 Changes:
  - Critical engine alerts via Telegram (instant)
  - Brand engagement anomaly alerts via Telegram
  - Daily 8am briefing via Telegram (health, tasks, backup, monitor)
  - New API endpoints: /alerts/history, /engines, /daily-briefing
  - Monitor plugin: site-down Telegram alerts
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import logging
import os
import random
import signal
import threading
import time
import traceback
import uuid
from collections import Counter, deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, TypeVar

# ═══════════════════════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=os.getenv("DIRECTOR_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("kanyoza.director")

T = TypeVar("T")


# ═══════════════════════════════════════════════════════════════════════════
# UTILITIES
# ═══════════════════════════════════════════════════════════════════════════

def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, deque)):
        return [safe(v) for v in value]
    return str(value)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


# ═══════════════════════════════════════════════════════════════════════════
# ERROR CLASSIFICATION
# ═══════════════════════════════════════════════════════════════════════════

class SupabaseErrorClassifier:
    @staticmethod
    def is_retryable(error: Exception) -> bool:
        status = SupabaseErrorClassifier._extract_status(error)
        if status == 429: return True
        if status and 400 <= status < 500: return False
        if status and status >= 500: return True
        error_str = str(error).lower()
        if any(x in error_str for x in ('timeout', 'connection', 'network', 'dns', 'reset', 'refused', 'unreachable', 'temporary')): return True
        return False

    @staticmethod
    def is_permanent(error: Exception) -> bool:
        status = SupabaseErrorClassifier._extract_status(error)
        return status is not None and 400 <= status < 500 and status != 429

    @staticmethod
    def _extract_status(error: Exception) -> int | None:
        if hasattr(error, 'code'):
            code = getattr(error, 'code', None)
            if isinstance(code, int): return code
            if isinstance(code, str) and code.isdigit(): return int(code)
        if hasattr(error, 'response') and hasattr(error.response, 'status_code'): return error.response.status_code
        if hasattr(error, 'status_code'): return error.status_code
        return None

    @staticmethod
    def describe(error: Exception) -> str:
        status = SupabaseErrorClassifier._extract_status(error)
        if status == 403: return "403 Forbidden — Use service_role key"
        if status == 401: return "401 Unauthorized"
        if status == 404: return "404 Not Found"
        if status == 429: return "429 Rate Limited"
        if status and status >= 500: return f"{status} Server Error"
        return str(error)[:300]


# ═══════════════════════════════════════════════════════════════════════════
# CIRCUIT BREAKER
# ═══════════════════════════════════════════════════════════════════════════

class CircuitBreaker:
    def __init__(self, name: str = "default", failure_threshold: int = 5, recovery_timeout: float = 30.0):
        self.name = name; self.failure_threshold = failure_threshold; self.recovery_timeout = recovery_timeout
        self._failure_count = 0; self._last_failure_time = 0.0; self._state = "CLOSED"; self._lock = threading.RLock()

    @property
    def is_open(self) -> bool:
        with self._lock:
            if self._state == "CLOSED": return False
            if self._state == "OPEN":
                if time.monotonic() - self._last_failure_time >= self.recovery_timeout:
                    self._state = "HALF_OPEN"; log.info("Circuit breaker '%s': OPEN → HALF_OPEN", self.name)
                    return False
                return True
            return False

    def record_success(self) -> None:
        with self._lock:
            if self._state == "HALF_OPEN": self._state = "CLOSED"; self._failure_count = 0; log.info("Circuit breaker '%s': HALF_OPEN → CLOSED", self.name)
            elif self._state == "CLOSED": self._failure_count = 0

    def record_failure(self) -> None:
        with self._lock:
            self._failure_count += 1; self._last_failure_time = time.monotonic()
            if self._state == "HALF_OPEN": self._state = "OPEN"; log.warning("Circuit breaker '%s': HALF_OPEN → OPEN", self.name)
            elif self._failure_count >= self.failure_threshold and self._state == "CLOSED": self._state = "OPEN"; log.error("Circuit breaker '%s': CLOSED → OPEN (%d failures)", self.name, self._failure_count)

    @property
    def state(self) -> str:
        with self._lock: return self._state


# ═══════════════════════════════════════════════════════════════════════════
# ENUMS
# ═══════════════════════════════════════════════════════════════════════════

class Authority(str, Enum): AUTO = "auto"; NOTIFY = "notify"; SUGGEST = "suggest"; ALERT = "alert"
class Sector(str, Enum): CONTENT = "content"; BRANDS = "brands"; HEALTH = "health"; SECURITY = "security"; FINANCE = "finance"; GROWTH = "growth"; LEARNING = "learning"; CLIENTS = "clients"; INFRA = "infra"; SELF = "self_improvement"
class Status(str, Enum): PLANNED = "planned"; APPROVAL = "approval_required"; APPROVED = "approved"; EXECUTING = "executing"; COMPLETED = "completed"; FAILED = "failed"; SKIPPED = "skipped"; FLAGGED = "flagged"; CANCELLED = "cancelled"
class Health(str, Enum): NOMINAL = "nominal"; DEGRADED = "degraded"; CRITICAL = "critical"; OFFLINE = "offline"; UNKNOWN = "unknown"
class Risk(str, Enum): LOW = "low"; MEDIUM = "medium"; HIGH = "high"; CRITICAL = "critical"
class Briefing(str, Enum): MORNING = "morning"; CHECKPOINT = "checkpoint"; EVENING = "evening"; ALERT = "alert"; WEEKLY = "weekly"


# ═══════════════════════════════════════════════════════════════════════════
# DATA MODELS
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class Capability:
    name: str; sector: Sector; description: str; handler: Callable[[Dict[str, Any]], Any]
    authority: Authority = Authority.SUGGEST; risk: Risk = Risk.MEDIUM; enabled: bool = True

@dataclass
class Observation:
    source: str; sector: Sector; status: Health = Health.UNKNOWN
    metrics: Dict[str, Any] = field(default_factory=dict); issues: List[Dict[str, Any]] = field(default_factory=list)
    message: str = ""; timestamp: datetime = field(default_factory=now)

@dataclass
class Task:
    id: str; sector: Sector; description: str; capability: str; authority: Authority; risk: Risk
    params: Dict[str, Any] = field(default_factory=dict); status: Status = Status.PLANNED
    created_at: datetime = field(default_factory=now); executed_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None; approved_by: Optional[str] = None
    result: Any = None; error: Optional[str] = None; confidence: float = 0.8; reason: str = ""; sources: List[str] = field(default_factory=list)

@dataclass
class Decision:
    id: str; timestamp: datetime; decision: str; reason: str; confidence: float
    sector: Sector; authority: Authority; risk: Risk; task_id: Optional[str] = None; outcome: Optional[str] = None

@dataclass
class Goal:
    id: str; name: str; description: str; sector: Sector; priority: int = 50; enabled: bool = True


# ═══════════════════════════════════════════════════════════════════════════
# SUPABASE STORE
# ═══════════════════════════════════════════════════════════════════════════

class SupabaseStore:
    def __init__(self, supabase_client):
        self.supabase = supabase_client; self.circuit_breaker = CircuitBreaker(name="supabase_store"); self._ensure_tables()

    def _ensure_tables(self) -> None:
        if self.circuit_breaker.is_open: return
        try: self.supabase.rpc("director_ensure_tables", {}).execute(); self.circuit_breaker.record_success()
        except Exception as exc: self.circuit_breaker.record_failure(); log.warning("Supabase table check failed: %s", exc)

    def _execute_with_retry(self, operation_name: str, fn: Callable[[], T], max_retries: int = 2) -> Optional[T]:
        if self.circuit_breaker.is_open: return None
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                result = fn(); self.circuit_breaker.record_success(); return result
            except Exception as exc:
                last_error = exc; self.circuit_breaker.record_failure()
                status = SupabaseErrorClassifier._extract_status(exc)
                if SupabaseErrorClassifier.is_permanent(exc): log.error("Director DB: '%s' FAILED (HTTP %s, permanent): %s", operation_name, status, SupabaseErrorClassifier.describe(exc)); return None
                if attempt >= max_retries: log.error("Director DB: '%s' FAILED after %d retries (HTTP %s)", operation_name, max_retries, status); return None
                delay = min(1.0 * (2 ** attempt), 10.0); delay *= random.uniform(0.75, 1.25)
                log.warning("Director DB: '%s' attempt %d/%d failed (HTTP %s). Retrying in %.1fs...", operation_name, attempt + 1, max_retries, status, delay)
                time.sleep(delay)
        return None

    def _table(self, name: str): return self.supabase.table(name)

    def _insert(self, table: str, data: dict) -> None:
        self._execute_with_retry(f"insert into {table}", lambda: self._table(table).upsert(data).execute(), max_retries=2)

    def _get(self, table: str, key: str, value: str) -> Optional[dict]:
        result = self._execute_with_retry(f"get from {table}", lambda: self._table(table).select("*").eq(key, value).limit(1).execute(), max_retries=1)
        return dict(result.data[0]) if result and result.data and len(result.data) > 0 else None

    def _list(self, table: str, limit: int = 100) -> List[dict]:
        result = self._execute_with_retry(f"list from {table}", lambda: self._table(table).select("*").order("created_at", desc=True).limit(limit).execute(), max_retries=1)
        return [dict(row) for row in (result.data or [])] if result else []

    def save_task(self, task: Task) -> None: self._insert("director_tasks", {"id": task.id, "payload": json.dumps(safe(asdict(task))), "created_at": iso(task.created_at), "status": task.status.value})
    def get_task(self, task_id: str) -> Optional[dict]: return self._get("director_tasks", "id", task_id)
    def list_tasks(self) -> List[dict]: return self._list("director_tasks", 200)
    def save_decision(self, decision: Decision) -> None: self._insert("director_decisions", {"id": decision.id, "payload": json.dumps(safe(asdict(decision))), "timestamp": iso(decision.timestamp)})
    def list_decisions(self, limit: int = 100) -> List[dict]: return self._list("director_decisions", limit)
    def save_observation(self, observation: Observation) -> None: self._insert("director_observations", {"payload": json.dumps(safe(asdict(observation))), "timestamp": iso(observation.timestamp)})
    def list_observations(self, limit: int = 100) -> List[dict]: return self._list("director_observations", limit)
    def save_approval(self, task_id: str, approved: bool, actor: str) -> None: self._insert("director_approvals", {"task_id": task_id, "approved": approved, "actor": actor, "timestamp": iso_now()})
    def get_approval(self, task_id: str) -> Optional[dict]: return self._get("director_approvals", "task_id", task_id)
    def save_learning(self, entry: dict) -> None: self._insert("director_learnings", {"id": entry.get("id", f"learn_{uuid.uuid4().hex[:12]}"), "payload": json.dumps(safe(entry)), "timestamp": iso_now()})
    def list_learnings(self, limit: int = 100) -> List[dict]: return self._list("director_learnings", limit)


# ═══════════════════════════════════════════════════════════════════════════
# IN-MEMORY STORE
# ═══════════════════════════════════════════════════════════════════════════

class InMemoryStore:
    def __init__(self): self.tasks: Dict[str, dict] = {}; self.decisions: List[dict] = []; self.observations: List[dict] = []; self.approvals: Dict[str, dict] = {}; self.learnings: List[dict] = []
    def save_task(self, task: Task) -> None: self.tasks[task.id] = safe(asdict(task))
    def get_task(self, task_id: str) -> Optional[dict]: return self.tasks.get(task_id)
    def list_tasks(self) -> List[dict]: return list(self.tasks.values())
    def save_decision(self, decision: Decision) -> None: self.decisions.append(safe(asdict(decision)))
    def list_decisions(self, limit: int = 100) -> List[dict]: return self.decisions[-limit:]
    def save_observation(self, observation: Observation) -> None: self.observations.append(safe(asdict(observation)))
    def list_observations(self, limit: int = 100) -> List[dict]: return self.observations[-limit:]
    def save_approval(self, task_id: str, approved: bool, actor: str) -> None: self.approvals[task_id] = {"task_id": task_id, "approved": approved, "actor": actor, "timestamp": iso_now()}
    def get_approval(self, task_id: str) -> Optional[dict]: return self.approvals.get(task_id)
    def save_learning(self, entry: dict) -> None: self.learnings.append(entry)
    def list_learnings(self, limit: int = 100) -> List[dict]: return self.learnings[-limit:]


# ═══════════════════════════════════════════════════════════════════════════
# POLICY ENGINE
# ═══════════════════════════════════════════════════════════════════════════

class Policy:
    CRITICAL = {"delete_brand", "delete_client_data", "drop_database", "reset_database", "rotate_credentials", "change_production_secrets", "disable_security_controls", "change_billing", "refund_client", "send_legal_notice", "deploy_breaking_change", "change_access_control"}
    def authority(self, capability: Capability) -> Authority:
        if not capability.enabled: return Authority.ALERT
        if capability.name in self.CRITICAL or capability.risk == Risk.CRITICAL: return Authority.SUGGEST
        return capability.authority
    def requires_approval(self, capability: Capability) -> bool:
        return capability.name in self.CRITICAL or capability.risk == Risk.CRITICAL or self.authority(capability) in (Authority.SUGGEST, Authority.ALERT)


# ═══════════════════════════════════════════════════════════════════════════
# FUNCTION OBSERVER
# ═══════════════════════════════════════════════════════════════════════════

class FunctionObserver:
    def __init__(self, name: str, sector: Sector, fn: Callable): self.name = name; self.sector = sector; self.fn = fn
    def observe(self) -> Observation:
        try: result = self.fn() or {}
        except Exception as exc: return Observation(self.name, self.sector, Health.CRITICAL, issues=[{"type": "observer_failure", "error": str(exc)}])
        if isinstance(result, Observation): return result
        status_str = str(result.get("status", "unknown")).lower()
        status_map = {"ok": Health.NOMINAL, "healthy": Health.NOMINAL, "nominal": Health.NOMINAL, "degraded": Health.DEGRADED, "critical": Health.CRITICAL, "offline": Health.OFFLINE}
        return Observation(self.name, self.sector, status_map.get(status_str, Health.UNKNOWN), result.get("metrics", {}), result.get("issues", []), result.get("message", ""))


# ═══════════════════════════════════════════════════════════════════════════
# ENGINE AUTO-DISCOVERY
# ═══════════════════════════════════════════════════════════════════════════

ENGINE_REGISTRY = [
    ("services.content_engine", "content_engine", "content_engine", Sector.CONTENT),
    ("services.card_renderer", "card_renderer", "card_renderer", Sector.CONTENT),
    ("services.condition_engine", "autonomous_engine", "condition_engine", Sector.LEARNING),
    ("services.email_service", "email_service", "email_service", Sector.CLIENTS),
    ("services.engagement_tracker", "engagement_tracker", "engagement_tracker", Sector.GROWTH),
    ("services.guardian_service", "guardian_service", "guardian", Sector.SECURITY),
    ("services.meta_engine", "meta_engine", "meta_engine", Sector.HEALTH),
    ("services.director", "director", "meta_director", Sector.SELF),
    ("ai.orchestrator", "AIOrchestrator", "ai_orchestrator", Sector.HEALTH),
    ("scheduler.brand_scheduler", "BrandSchedulerManager", "scheduler", Sector.CONTENT),
    ("services.job_service", "JobService", "job_service", Sector.INFRA),
    ("plugins.base", "PluginRegistry", "plugin_registry", Sector.INFRA),
    ("core.event_bus", "EventBus", "event_bus", Sector.INFRA),
    ("core.render_queue", "get_render_queue", "render_queue", Sector.CONTENT),
    ("services.image_engine", "generate_best_image", "image_engine", Sector.CONTENT),
    ("domain.content.topic_selector", "TopicSelector", "topic_selector", Sector.CONTENT),
    ("services.prompt_builder", "prompt_builder", "prompt_builder", Sector.CONTENT),
    ("services.health_monitor_service", "HealthMonitorService", "health_monitor", Sector.HEALTH),
]

def _make_observer(engine_name: str, engine_obj: Any) -> Callable:
    def observe() -> dict:
        if hasattr(engine_obj, "get_status"):
            try:
                r = engine_obj.get_status()
                if isinstance(r, dict): return {"status": r.get("overall", r.get("status", "healthy")), "metrics": r}
            except Exception: pass
        if hasattr(engine_obj, "health_check"):
            try:
                r = engine_obj.health_check()
                if isinstance(r, dict): return {"status": r.get("status", "healthy"), "metrics": r}
            except Exception: pass
        try:
            metrics = {}
            for attr in ("_decisions", "_strategies", "_observations", "_engines", "rules", "tasks"):
                if hasattr(engine_obj, attr): val = getattr(engine_obj, attr); metrics[attr] = len(val) if hasattr(val, "__len__") else 1
            if metrics: return {"status": "healthy", "metrics": metrics}
        except Exception: pass
        return {"status": "healthy", "metrics": {"engine": engine_name}}
    return observe

def auto_discover_engines(director: 'Director', container) -> int:
    discovered = 0
    for module_path, attr_name, display_name, sector in ENGINE_REGISTRY:
        try:
            module = __import__(module_path, fromlist=[attr_name]); engine = getattr(module, attr_name, None)
            if engine is not None: director.register_observer(FunctionObserver(display_name, sector, _make_observer(display_name, engine))); discovered += 1; log.debug("👔 Director: Auto-discovered %s (%s)", display_name, module_path)
        except ImportError: log.debug("👔 Director: %s not available (%s)", display_name, module_path)
        except Exception as exc: log.debug("👔 Director: %s failed to load: %s", display_name, exc)
    plugins = getattr(container, "industry_plugins", {})
    for name, plugin in plugins.items():
        sector = _infer_plugin_sector(name); director.register_observer(FunctionObserver(name, sector, _make_observer(name, plugin))); discovered += 1
    log.info("👔 Director: Auto-discovered %d engines (%d core + %d plugins)", discovered, discovered - len(plugins), len(plugins))
    return discovered

def _infer_plugin_sector(name: str) -> Sector:
    name_lower = name.lower()
    if any(kw in name_lower for kw in ("church", "school")): return Sector.BRANDS
    if any(kw in name_lower for kw in ("hospital", "clinic", "health")): return Sector.HEALTH
    if any(kw in name_lower for kw in ("crm", "client", "customer")): return Sector.CLIENTS
    return Sector.INFRA


# ═══════════════════════════════════════════════════════════════════════════
# THE DIRECTOR v1.2 — with Telegram alerts, daily briefing, brand monitoring
# ═══════════════════════════════════════════════════════════════════════════

class Director:
    VERSION = "1.2.0"

    def __init__(self, supabase_client=None, interval: int = 60):
        self.store = SupabaseStore(supabase_client) if supabase_client else InMemoryStore()
        if supabase_client: log.info("Director storage: Supabase (PostgreSQL)")
        else: log.warning("Director storage: In-Memory (Supabase not configured)")
        self.interval = max(10, interval); self.policy = Policy(); self.lock = threading.RLock()
        self.running = False; self.thread: Optional[threading.Thread] = None; self.started = now()
        self.observers: Dict[str, FunctionObserver] = {}; self.capabilities: Dict[str, Capability] = {}; self.tasks: Dict[str, Task] = {}; self.goals: Dict[str, Goal] = {}
        self.history: deque = deque(maxlen=5000); self.decisions: deque = deque(maxlen=2000); self.observations: Dict[str, Observation] = {}; self.briefings: deque = deque(maxlen=200)
        self.last_plan: Optional[datetime] = None; self.last_observation: Optional[datetime] = None
        self.alert_cb: Optional[Callable] = None; self.event_cb: Optional[Callable] = None
        self._last_daily_briefing: Optional[str] = None
        self._alert_history: deque = deque(maxlen=500)
        self._init_goals(); self._init_capabilities()
        log.info("👔 KANYOZA DIRECTOR v%s initialized", self.VERSION)

    def _init_goals(self) -> None:
        for gid, name, sector, pri in [("system_stability", "System stability", Sector.HEALTH, 100), ("security", "Security posture", Sector.SECURITY, 100), ("clients", "Client commitments", Sector.CLIENTS, 90), ("brands", "Brand health", Sector.BRANDS, 85), ("content", "Content consistency", Sector.CONTENT, 80), ("growth", "Business growth", Sector.GROWTH, 70), ("learning", "Continuous learning", Sector.LEARNING, 60)]:
            self.goals[gid] = Goal(gid, name, name, sector, pri)

    def _init_capabilities(self) -> None:
        for name, sector, desc, auth, risk in [("publish_scheduled_content", Sector.CONTENT, "Publish scheduled content", Authority.AUTO, Risk.LOW), ("optimize_content_schedule", Sector.CONTENT, "Adjust safe scheduling", Authority.NOTIFY, Risk.LOW), ("calculate_brand_health", Sector.BRANDS, "Calculate brand health", Authority.AUTO, Risk.LOW), ("queue_brand_revival", Sector.BRANDS, "Queue bounded revival", Authority.NOTIFY, Risk.MEDIUM), ("run_health_scan", Sector.HEALTH, "Run platform health scan", Authority.AUTO, Risk.LOW), ("restart_failed_service", Sector.HEALTH, "Restart isolated failed service", Authority.AUTO, Risk.MEDIUM), ("run_guardian_scan", Sector.SECURITY, "Run security scan", Authority.AUTO, Risk.LOW), ("quarantine_security_issue", Sector.SECURITY, "Quarantine confirmed issue", Authority.NOTIFY, Risk.HIGH), ("prepare_security_remediation", Sector.SECURITY, "Prepare security remediation", Authority.SUGGEST, Risk.HIGH), ("generate_client_report", Sector.CLIENTS, "Generate client report", Authority.AUTO, Risk.LOW), ("prepare_client_message", Sector.CLIENTS, "Prepare client message", Authority.SUGGEST, Risk.MEDIUM), ("analyze_growth_opportunity", Sector.GROWTH, "Analyze growth", Authority.AUTO, Risk.LOW), ("optimize_infrastructure", Sector.INFRA, "Prepare infrastructure optimization", Authority.SUGGEST, Risk.HIGH), ("run_learning_cycle", Sector.LEARNING, "Learn from outcomes", Authority.AUTO, Risk.LOW), ("self_diagnostic", Sector.SELF, "Diagnose Director", Authority.AUTO, Risk.LOW)]:
            self.register_capability(Capability(name, sector, desc, self._placeholder(name), auth, risk))
        for name, sector in [("delete_brand", Sector.BRANDS), ("delete_client_data", Sector.CLIENTS), ("drop_database", Sector.INFRA), ("reset_database", Sector.INFRA), ("rotate_credentials", Sector.SECURITY), ("change_production_secrets", Sector.SECURITY), ("disable_security_controls", Sector.SECURITY), ("change_billing", Sector.FINANCE), ("refund_client", Sector.FINANCE), ("send_legal_notice", Sector.CLIENTS), ("deploy_breaking_change", Sector.INFRA), ("change_access_control", Sector.SECURITY)]:
            self.register_capability(Capability(name, sector, name.replace("_", " ").title(), self._blocked, Authority.SUGGEST, Risk.CRITICAL))

    def _placeholder(self, name: str) -> Callable: return lambda p: {"status": "delegated", "capability": name, "message": "Bind the real Kanyoza service handler before production execution.", "params": p}
    def _blocked(self, p: dict) -> dict: return {"status": "blocked", "reason": "Critical action requires human approval and a bound production handler."}

    def register_observer(self, observer: FunctionObserver) -> None: self.observers[observer.name] = observer
    def register_capability(self, capability: Capability) -> None: self.capabilities[capability.name] = capability
    def set_alert_callback(self, cb: Callable) -> None: self.alert_cb = cb
    def set_event_callback(self, cb: Callable) -> None: self.event_cb = cb

    def observe_all(self) -> Dict[str, Observation]:
        for name, observer in list(self.observers.items()):
            obs = observer.observe(); self.observations[name] = obs; self.store.save_observation(obs)
        self.last_observation = now()
        # ── Check criticals after every observation cycle ──
        self._check_critical_and_alert()
        return self.observations

    def health(self) -> dict:
        weights = {Health.NOMINAL: 100, Health.DEGRADED: 70, Health.CRITICAL: 25, Health.OFFLINE: 0, Health.UNKNOWN: 50}
        groups: Dict[str, List[Observation]] = {}
        for obs in self.observations.values(): groups.setdefault(obs.sector.value, []).append(obs)
        sectors = {}; scores = []
        for sn, ol in groups.items():
            score = sum(weights[o.status] for o in ol) / len(ol); scores.append(score)
            sectors[sn] = {"status": self._status_label(score).value, "score": round(score, 1), "issues": sum(len(o.issues) for o in ol), "observers": len(ol)}
        overall = sum(scores) / len(scores) if scores else 50
        return {"status": self._status_label(overall).value, "score": round(overall, 1), "sectors": sectors, "observer_count": len(self.observers), "issue_count": sum(len(o.issues) for o in self.observations.values()), "timestamp": iso(now())}

    def _status_label(self, score: float) -> Health:
        if score >= 90: return Health.NOMINAL
        if score >= 60: return Health.DEGRADED
        if score > 0: return Health.CRITICAL
        return Health.OFFLINE

    # ── NOTIFICATION ENGINE ──────────────────────────────────────────

    def _send_telegram(self, message: str) -> bool:
        try:
            from services.email_service import email_service
            return email_service.send_telegram(message)
        except Exception as exc:
            log.error("Telegram send failed: %s", exc); return False

    def _check_critical_and_alert(self) -> None:
        criticals = []
        for name, obs in self.observations.items():
            if obs.status in (Health.CRITICAL, Health.OFFLINE):
                criticals.append(f"• {name} ({obs.sector.value}): {obs.status.value}")
        if criticals:
            msg = f"🔴 <b>CRITICAL — {len(criticals)} engine(s) down</b>\n\n" + "\n".join(criticals) + f"\n\n<i>{now().strftime('%H:%M:%S UTC')}</i>"
            self._send_telegram(msg)
            self._alert_history.append({"type": "critical", "engines": criticals, "time": iso(now())})
        self._check_brand_alerts()

    def _check_brand_alerts(self) -> None:
        try:
            for name, obs in self.observations.items():
                if obs.sector not in (Sector.BRANDS, Sector.CONTENT): continue
                metrics = obs.metrics or {}; change = metrics.get("engagement_change_pct", 0)
                if change <= -50:
                    self._send_telegram(f"📉 <b>Engagement Drop: {name}</b>\nDown {abs(change):.0f}%\n<i>{now().strftime('%H:%M UTC')}</i>")
                    self._alert_history.append({"type": "brand_drop", "brand": name, "change_pct": change, "time": iso(now())})
                elif change >= 200:
                    self._send_telegram(f"🔥 <b>Viral: {name}</b>\nUp {change:.0f}% — boosting\n<i>{now().strftime('%H:%M UTC')}</i>")
                    self._alert_history.append({"type": "brand_viral", "brand": name, "change_pct": change, "time": iso(now())})
        except Exception as exc: log.debug("Brand alert check: %s", exc)

    def _send_daily_briefing(self) -> None:
        try:
            health = self.health()
            tasks_done = sum(1 for t in self.history if t.status == Status.COMPLETED)
            tasks_failed = sum(1 for t in self.history if t.status == Status.FAILED)
            backup_status = "Unknown"; monitor_status = "Unknown"
            try:
                from plugins.backup.plugin import backup_plugin
                if backup_plugin and backup_plugin.supabase:
                    records = backup_plugin.supabase.table("backup_records").select("*").order("started_at", desc=True).limit(1).execute()
                    if records.data:
                        last = records.data[0]; hours_ago = round((now() - datetime.fromisoformat(last["started_at"].replace("Z", "+00:00"))).total_seconds() / 3600)
                        backup_status = f"{last['status']} ({hours_ago}h ago)" if last['status'] == 'completed' else f"❌ {last['status']}"
            except Exception: pass
            try:
                from plugins.monitor.plugin import monitor_plugin
                if monitor_plugin and monitor_plugin.supabase:
                    targets = monitor_plugin.supabase.table("monitor_targets").select("*").eq("is_paused", False).execute()
                    if targets.data:
                        checks = monitor_plugin.supabase.table("monitor_checks").select("status,target_id").order("checked_at", desc=True).limit(len(targets.data) * 2).execute()
                        up_count = sum(1 for c in (checks.data or []) if c["status"] == "up"); total = len(targets.data)
                        monitor_status = f"{up_count}/{total} UP" if up_count == total else f"❌ {up_count}/{total} UP"
            except Exception: pass
            msg = f"""📊 <b>KANYOZA DAILY BRIEFING</b>
━━━━━━━━━━━━━━━━━━━
✅ Health: {health['status'].upper()} ({health['score']:.0f}%)
📝 Tasks: {tasks_done} done, {tasks_failed} failed
💾 Backup: {backup_status}
🔍 Monitor: {monitor_status}
━━━━━━━━━━━━━━━━━━━
👔 {len(self.observers)} engines | v{self.VERSION}"""
            self._send_telegram(msg); log.info("Daily briefing sent via Telegram")
        except Exception as exc: log.error("Daily briefing failed: %s", exc)

    # ── DECISION & PLANNING ──────────────────────────────────────────

    def _create_task(self, sector: Sector, desc: str, cap_name: str, reason: str, confidence: float = 0.8, params: dict = None, source: str = None) -> Task:
        cap = self.capabilities[cap_name]; authority = self.policy.authority(cap)
        task = Task(id=f"dir_{uuid.uuid4().hex[:14]}", sector=sector, description=desc, capability=cap_name, authority=authority, risk=cap.risk, params=params or {}, status=Status.APPROVAL if self.policy.requires_approval(cap) else Status.PLANNED, confidence=confidence, reason=reason, sources=[source] if source else [])
        self.tasks[task.id] = task; self.store.save_task(task); return task

    def decide(self) -> List[Task]:
        decisions = []; health = self.health()
        if health["score"] < 90: decisions.append(self._create_task(Sector.HEALTH, "Run complete platform health scan", "run_health_scan", f"Health score is {health['score']}", 0.99))
        for obs in self.observations.values():
            for issue in obs.issues:
                typ = str(issue.get("type", "")).lower()
                if typ in {"failed_service", "service_down", "worker_failed"}: decisions.append(self._create_task(Sector.HEALTH, "Recover failed service", "restart_failed_service", "Recoverable service failure", 0.9, issue, obs.source))
                elif typ in {"security_issue", "vulnerability", "credential_exposure"}: decisions.append(self._create_task(Sector.SECURITY, "Prepare security remediation", "prepare_security_remediation", "Security issue observed", 0.97, issue, obs.source))
                elif typ in {"brand_inactive", "engagement_decline"}: decisions.append(self._create_task(Sector.BRANDS, "Queue brand revival", "queue_brand_revival", "Brand performance declined", 0.87, issue, obs.source))
                elif typ in {"content_gap", "schedule_gap"}: decisions.append(self._create_task(Sector.CONTENT, "Optimize content schedule", "optimize_content_schedule", "Content gap observed", 0.88, issue, obs.source))
                elif typ == "client_report_due": decisions.append(self._create_task(Sector.CLIENTS, "Generate due client report", "generate_client_report", "Client report due", 0.96, issue, obs.source))
        for goal in sorted(self.goals.values(), key=lambda g: g.priority, reverse=True):
            if not goal.enabled: continue
            mapping = {Sector.SECURITY: ("run_guardian_scan", 0.93), Sector.BRANDS: ("calculate_brand_health", 0.88), Sector.CONTENT: ("publish_scheduled_content", 0.80), Sector.GROWTH: ("analyze_growth_opportunity", 0.82), Sector.CLIENTS: ("generate_client_report", 0.78)}
            if goal.sector in mapping:
                cap_name, conf = mapping[goal.sector]; decisions.append(self._create_task(goal.sector, f"Goal-driven: {goal.name}", cap_name, f"Goal: {goal.name}", conf))
        if not decisions: decisions.append(self._create_task(Sector.SELF, "Run Director self-diagnostic", "self_diagnostic", "No urgent work; inspect own state", 0.95))
        seen = set(); unique = []
        for task in decisions:
            key = (task.sector, task.capability, task.description)
            if key not in seen: seen.add(key); unique.append(task)
        return sorted(unique, key=self._priority, reverse=True)

    def _priority(self, task: Task) -> float:
        return ({Authority.ALERT: 1000, Authority.SUGGEST: 700, Authority.NOTIFY: 500, Authority.AUTO: 300}[task.authority] + {Risk.CRITICAL: 100, Risk.HIGH: 70, Risk.MEDIUM: 40, Risk.LOW: 10}[task.risk] + task.confidence * 100)

    def build_plan(self) -> dict:
        tasks = self.decide(); self.last_plan = now()
        return {"date": now().date().isoformat(), "created_at": iso(self.last_plan), "tasks": [safe(asdict(t)) for t in tasks], "strategy": "Observe → assess → prioritize → authorize → execute → verify → learn → report", "priorities": ["availability", "security", "client commitments", "brand/content operations", "growth", "learning"]}

    def approve(self, task_id: str, actor: str = "admin") -> Task:
        task = self.tasks[task_id]
        if task.status not in (Status.APPROVAL, Status.FLAGGED): raise ValueError("Task is not awaiting approval")
        task.status = Status.APPROVED; task.approved_at = now(); task.approved_by = actor; self.store.save_task(task); self.store.save_approval(task_id, True, actor); return task

    def reject(self, task_id: str, actor: str = "admin", reason: str = "") -> Task:
        task = self.tasks[task_id]; task.status = Status.CANCELLED; task.error = f"Rejected by {actor}: {reason}"; self.store.save_task(task); self.store.save_approval(task_id, False, actor); return task

    def execute(self, task_id: str) -> Task:
        task = self.tasks[task_id]; cap = self.capabilities[task.capability]
        if not cap.enabled: task.status = Status.SKIPPED; task.error = "Capability disabled"; self.store.save_task(task); return task
        if self.policy.requires_approval(cap) and task.status != Status.APPROVED: task.status = Status.APPROVAL; self.store.save_task(task); self._alert({"type": "approval_required", "task": safe(asdict(task))}); return task
        task.status = Status.EXECUTING; task.executed_at = now(); self.store.save_task(task)
        try:
            result = cap.handler(task.params)
            if inspect.isawaitable(result): result = asyncio.run(result)
            task.status = Status.COMPLETED; task.result = safe(result or {}); self.history.append(task); self.store.save_task(task)
            self._record_decision(f"Executed: {task.description}", task.reason, task); self._learn(task); self._event("director_action_completed", {"task": safe(asdict(task))}); return task
        except Exception as exc:
            task.status = Status.FAILED; task.error = str(exc); task.result = {"traceback": traceback.format_exc(limit=5)}; self.history.append(task); self.store.save_task(task)
            self._record_decision(f"Failed: {task.description}", str(exc), task, "failed"); self._learn(task); self._alert({"type": "task_failed", "task": safe(asdict(task))}); return task

    def execute_due(self) -> None:
        for task in list(self.tasks.values()):
            if task.status in (Status.PLANNED, Status.APPROVED): self.execute(task.id)

    def _record_decision(self, decision_text: str, reason: str, task: Task, outcome: str = None) -> None:
        d = Decision(id=f"decision_{uuid.uuid4().hex[:12]}", timestamp=now(), decision=decision_text, reason=reason, confidence=task.confidence, sector=task.sector, authority=task.authority, risk=task.risk, task_id=task.id, outcome=outcome)
        self.decisions.append(d); self.store.save_decision(d)

    def _learn(self, task: Task) -> None:
        self.store.save_learning({"id": f"learn_{uuid.uuid4().hex[:12]}", "task_id": task.id, "signal": "success" if task.status == Status.COMPLETED else "failure", "lesson": f"Outcome of {task.capability}: {task.status.value}"})

    def _alert(self, payload: dict) -> None:
        self._event("director_alert", payload); self._alert_history.append({"type": "director_alert", "payload": payload, "time": iso(now())})
        if self.alert_cb:
            try: self.alert_cb(payload)
            except Exception: log.exception("Alert callback failed")

    def _event(self, name: str, payload: dict) -> None:
        if self.event_cb:
            try: self.event_cb(name, payload)
            except Exception: log.exception("Event callback failed")

    def briefing(self, kind: Briefing = Briefing.CHECKPOINT) -> dict:
        health = self.health(); pending = [safe(asdict(t)) for t in self.tasks.values() if t.status == Status.APPROVAL]; failures = [t for t in self.history if t.status == Status.FAILED]
        brief = {"type": kind.value, "timestamp": iso(now()), "mood": ("critical" if health["score"] < 40 else "attentive" if pending else "watchful" if failures else "confident" if health["score"] >= 90 else "focused"), "health": health, "actions_taken": [t.description for t in list(self.history)[-10:] if t.status == Status.COMPLETED], "pending_approvals": pending, "insights": [f"Health score: {health['score']}", f"Pending approvals: {len(pending)}", f"Recent failures: {len(failures)}"], "plan_summary": {"tasks": len(self.tasks), "approvals": len(pending)}}
        self.briefings.append(brief); self._event("director_briefing", brief); return brief

    def status(self) -> dict:
        health = self.health(); counts = Counter(t.status.value for t in self.tasks.values())
        return {"name": "Kanyoza Director", "codename": "The Autonomous CEO", "version": self.VERSION, "running": self.running, "uptime_hours": round((now() - self.started).total_seconds() / 3600, 2), "health": health, "observers": len(self.observers), "capabilities": len(self.capabilities), "sectors": len(Sector), "tasks": dict(counts), "pending_approvals": sum(t.status == Status.APPROVAL for t in self.tasks.values()), "last_observation": iso(self.last_observation), "last_plan": iso(self.last_plan)}

    def start(self) -> None:
        if self.running: return
        self.running = True; self.started = now()
        self.thread = threading.Thread(target=self._loop, name="kanyoza-director", daemon=True); self.thread.start()
        threading.Thread(target=self._startup, name="director-startup", daemon=True).start()
        log.info("👔 Director ONLINE — watching the platform")

    def _startup(self) -> None:
        try: self.observe_all(); self.build_plan(); self.execute_due(); self.briefing(Briefing.CHECKPOINT)
        except Exception: log.exception("Startup cycle failed")

    def stop(self) -> None:
        self.running = False
        if self.thread and self.thread.is_alive(): self.thread.join(timeout=5)
        log.info("👔 Director OFFLINE")

    def _loop(self) -> None:
        last_minute = None
        while self.running:
            try:
                self.observe_all()
                if self.last_plan is None or (now() - self.last_plan).total_seconds() >= 1800: self.build_plan()
                self.execute_due()
                current_minute = now().strftime("%Y-%m-%d-%H-%M")
                if current_minute != last_minute:
                    last_minute = current_minute; hour = now().hour; minute = now().minute
                    if hour == 8 and minute == 0:
                        today_str = now().strftime("%Y-%m-%d")
                        if self._last_daily_briefing != today_str: self._last_daily_briefing = today_str; self._send_daily_briefing()
                    if minute == 0 and hour in (6, 12, 18, 22):
                        kind = Briefing.MORNING if hour == 6 else Briefing.EVENING if hour == 22 else Briefing.CHECKPOINT
                        self.briefing(kind)
            except Exception: log.exception("Director cycle failed")
            time.sleep(self.interval)


# ═══════════════════════════════════════════════════════════════════════════
# FASTAPI APP — v1.2 endpoints
# ═══════════════════════════════════════════════════════════════════════════

def create_app(director: Director):
    from fastapi import FastAPI, HTTPException
    from pydantic import BaseModel

    app = FastAPI(title="Kanyoza Director", version=director.VERSION)

    class ApprovalBody(BaseModel): actor: str = "admin"
    class RejectBody(BaseModel): actor: str = "admin"; reason: str = ""

    @app.get("/status")
    def get_status(): return director.status()

    @app.get("/briefing")
    def get_briefing(): return director.briefings[-1] if director.briefings else director.briefing()

    @app.get("/tasks")
    def get_tasks(): return [safe(asdict(t)) for t in director.tasks.values()]

    @app.get("/approvals")
    def get_approvals(): return [safe(asdict(t)) for t in director.tasks.values() if t.status == Status.APPROVAL]

    @app.get("/decisions")
    def get_decisions(): return [safe(asdict(d)) for d in list(director.decisions)[-100:]]

    @app.get("/engines")
    def get_engines(): return {"engines": {name: {"sector": obs.sector.value, "status": obs.status.value, "issues": len(obs.issues)} for name, obs in director.observations.items()}}

    @app.get("/alerts/history")
    def get_alert_history(limit: int = 50): return {"alerts": list(director._alert_history)[-limit:]}

    @app.get("/daily-briefing")
    def get_daily_briefing():
        director._send_daily_briefing()
        return {"ok": True, "message": "Daily briefing sent via Telegram"}

    @app.post("/observe")
    def run_observe(): director.observe_all(); return director.status()

    @app.post("/plan")
    def run_plan(): return director.build_plan()

    @app.post("/start")
    def run_start(): director.start(); return director.status()

    @app.post("/stop")
    def run_stop(): director.stop(); return director.status()

    @app.post("/tasks/{task_id}/approve")
    def approve_task(task_id: str, body: ApprovalBody):
        try: return safe(asdict(director.approve(task_id, body.actor)))
        except KeyError: raise HTTPException(404, "Task not found")
        except ValueError as e: raise HTTPException(409, str(e))

    @app.post("/tasks/{task_id}/reject")
    def reject_task(task_id: str, body: RejectBody):
        try: return safe(asdict(director.reject(task_id, body.actor, body.reason)))
        except KeyError: raise HTTPException(404, "Task not found")

    return app


# ═══════════════════════════════════════════════════════════════════════════
# INITIALIZER
# ═══════════════════════════════════════════════════════════════════════════

director: Optional[Director] = None

def init_director(supabase_client=None, auto_start: bool = True) -> Director:
    global director
    if director is None: director = Director(supabase_client=supabase_client)
    if auto_start and not director.running: director.start()
    return director


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Kanyoza Director")
    parser.add_argument("--api", action="store_true", help="Run as API server")
    parser.add_argument("--host", default="0.0.0.0"); parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args()
    d = init_director(auto_start=True)
    if args.api:
        import uvicorn; app = create_app(d)
        signal.signal(signal.SIGTERM, lambda *_: d.stop()); signal.signal(signal.SIGINT, lambda *_: d.stop())
        uvicorn.run(app, host=args.host, port=args.port)
    else:
        try:
            while d.running: time.sleep(5)
        except KeyboardInterrupt: d.stop()

if __name__ == "__main__": main()
