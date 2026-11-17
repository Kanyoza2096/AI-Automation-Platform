"""
KANYOZA DIRECTOR — Core Engine (v5.5)

The Director class. Owns the main async loop, task lifecycle, capability
execution, decision making, learning loop integration, outcome verification,
and proposal generation.

v5.5 CHANGES:
  - Proposals: detectors produce signals, signals become proposals,
    proposals await human approval, approved proposals become tasks.
  - Proposal lifecycle: approve_proposal / reject_proposal methods.
  - Detector scheduling: run every 6 hours inside the main loop.
  - New capabilities: update_brand_posting_schedule, scale_worker_pool —
    both proposal_capable=True.
  - Outcome verification for restart, schedule change, worker scale.

v5.4 CHANGES (retained): outcome verification, VERIFIED/INEFFECTIVE
statuses, honest replay logging.
v5.3.x CHANGES (retained): target-scoped learning, persistent replay,
error classification.
"""

from __future__ import annotations

import asyncio
import inspect
import hashlib
import json
import logging
import os
import time
import uuid
from collections import Counter, defaultdict, deque
from dataclasses import asdict
from datetime import datetime, timezone, timedelta
from statistics import mean
from typing import Any, Callable, Dict, List, Optional

from .utils import (
    now,
    utcnow,
    safe,
    clamp,
    sanitize_error_message,
    iso,
    _publish_event,
    flush_pending_event_tasks,
)
from .models import (
    Authority,
    Briefing,
    Capability,
    Decision,
    DirectorSignal,
    Goal,
    Health,
    LearningSignal,
    Observation,
    Proposal,
    ProposalStatus,
    ResourceAllocation,
    Risk,
    RootCauseAnalysis,
    Sector,
    Status,
    StrategicOutlook,
    Task,
)
from .stores import AsyncSupabaseStore, InMemoryStore
from .analysis import (
    AdaptiveThresholds,
    CascadingFailurePredictor,
    CorrelatedRecovery,
    ENGINE_REGISTRY,
    FunctionObserver,
    ImpactAssessor,
    LearningLoop,
    Policy,
    RootCauseAnalyzer,
    SystemTopology,
)
from .proposals import (
    Detector,
    EngagementByHourDetector,
    LoadForecastDetector,
    ProposalGenerator,
)
from .planning import build_strategic_plan
from .plan_verifier import verify_strategic_plan as verify_plan_contract

log = logging.getLogger("kanyoza.director")

try:
    from opentelemetry import trace
    tracer = trace.get_tracer("kanyoza.director", "5.5.0")
    HAS_TRACING = True
except ImportError:
    tracer = None
    HAS_TRACING = False

try:
    from council.integration import get_council
    HAS_COUNCIL = True
except ImportError:
    HAS_COUNCIL = False

try:
    from core.unified_bus import get_unified_bus
    HAS_EVENT_BUS = True
except ImportError:
    HAS_EVENT_BUS = False


# ═══════════════════════════════════════════════════════════════════════════
# OUTCOME PREDICATES
# ═══════════════════════════════════════════════════════════════════════════

def _verify_restart(
    task_params: dict,
    pre_state: dict,
    post_state: dict,
) -> Optional[bool]:
    """Return True/False only with comparable evidence; None means unknown."""
    service_name = str(task_params.get("service_name") or "").strip()
    if not service_name:
        return None
    pre = pre_state.get(service_name)
    post = post_state.get(service_name)
    if not isinstance(pre, dict) or not isinstance(post, dict):
        return None
    pre_status = str(pre.get("status") or "").lower()
    post_status = str(post.get("status") or "").lower()
    known = {"nominal", "healthy", "ok", "degraded", "critical", "offline"}
    if pre_status not in known or post_status not in known:
        return None
    healthy = ("nominal", "healthy", "ok")
    if pre_status in healthy:
        return post_status in healthy
    if pre_status in ("critical", "offline"):
        return post_status in (*healthy, "degraded")
    if pre_status == "degraded":
        return post_status in (*healthy, "degraded")
    return False


def _authoritative_resource_state(
    snapshot: dict,
    resource_collection: str,
    resource_id: str,
) -> Optional[dict]:
    """Find a resource only in the explicit observer contract."""
    now_utc = datetime.now(timezone.utc)
    for observer in (snapshot or {}).values():
        if not isinstance(observer, dict):
            continue
        status = str(observer.get("status") or "unknown").lower()
        if status not in {"healthy", "nominal", "ok", "ready"}:
            continue
        observed_at = observer.get("observed_at")
        if not observed_at:
            continue
        try:
            parsed_at = datetime.fromisoformat(str(observed_at).replace("Z", "+00:00"))
            if parsed_at.tzinfo is None:
                parsed_at = parsed_at.replace(tzinfo=timezone.utc)
            age_seconds = (now_utc - parsed_at.astimezone(timezone.utc)).total_seconds()
        except (TypeError, ValueError):
            continue
        if age_seconds < -30 or age_seconds > 300:
            continue
        metrics = observer.get("metrics")
        if not isinstance(metrics, dict):
            continue
        collection = metrics.get(resource_collection)
        if not isinstance(collection, dict):
            continue
        resource = collection.get(resource_id)
        if isinstance(resource, dict):
            return resource
    return None


def _verify_schedule_change(
    task_params: dict,
    pre_state: dict,
    post_state: dict,
) -> Optional[bool]:
    """Verify the requested schedule through an authoritative observation."""
    brand_id = str(task_params.get("brand_id") or "").strip()
    workspace_id = str(task_params.get("workspace_id") or "").strip()
    requested_hours = task_params.get("posting_hours")
    if not brand_id or not isinstance(requested_hours, list) or not requested_hours:
        return False
    try:
        expected = sorted(int(hour) for hour in requested_hours)
    except (TypeError, ValueError):
        return False
    if any(hour < 0 or hour > 23 for hour in expected) or len(set(expected)) != len(expected):
        return False
    observed = _authoritative_resource_state(post_state, "brands", brand_id)
    if not observed:
        return None
    observed_workspace = str(observed.get("workspace_id") or "")
    if not observed_workspace:
        return None
    if workspace_id and observed_workspace != workspace_id:
        return False
    schedule = observed.get("posting_schedule")
    if not isinstance(schedule, dict) or "content_hours" not in schedule:
        return None
    actual = schedule.get("content_hours")
    if not isinstance(actual, list):
        return None
    try:
        actual_hours = sorted(int(hour) for hour in actual)
    except (TypeError, ValueError):
        return False
    return actual_hours == expected


def _verify_worker_scale(
    task_params: dict,
    pre_state: dict,
    post_state: dict,
) -> Optional[bool]:
    """Verify actual worker count from an authoritative pool observation."""
    pool_name = str(task_params.get("pool_name") or "").strip()
    workspace_id = str(task_params.get("workspace_id") or "").strip()
    target = task_params.get("target_workers")
    if not pool_name or isinstance(target, bool):
        return False
    try:
        target_workers = int(target)
    except (TypeError, ValueError):
        return False
    if target_workers < 1 or target_workers > 100:
        return False
    observed = _authoritative_resource_state(post_state, "worker_pools", pool_name)
    if not observed:
        return None
    observed_workspace = str(observed.get("workspace_id") or "")
    if not observed_workspace:
        return None
    if workspace_id and observed_workspace != workspace_id:
        return False
    actual = observed.get("worker_count")
    if actual is None or isinstance(actual, bool) or not isinstance(actual, int):
        return None
    return actual == target_workers


# ═══════════════════════════════════════════════════════════════════════════
# OBSERVER FACTORY & ENGINE AUTO-DISCOVERY
# ═══════════════════════════════════════════════════════════════════════════

def _make_observer(engine_name: str, engine_obj: Any) -> Callable:
    """Adapt an engine's explicit health contract without inventing health."""
    def reported_status(payload: dict) -> str:
        status = payload.get("status")
        if status is None:
            status = payload.get("overall")
        if hasattr(status, "value"):
            status = status.value
        if isinstance(status, str) and status.strip():
            return status.strip().lower()
        return "unknown"

    def observe() -> dict:
        for method_name in ("check_health_async", "health_check", "get_status"):
            method = getattr(engine_obj, method_name, None)
            if not callable(method):
                continue
            try:
                result = method()
                if inspect.isawaitable(result):
                    try:
                        asyncio.get_running_loop()
                    except RuntimeError:
                        result = asyncio.run(result)
                    else:
                        close = getattr(result, "close", None)
                        if callable(close):
                            close()
                        raise RuntimeError("async health probe must run outside an active event loop")
            except Exception:
                continue
            if isinstance(result, dict):
                return {
                    "contract_version": "kdo.observer.v1",
                    "status": reported_status(result),
                    "observed_at": result.get("observed_at") or result.get("timestamp"),
                    "metrics": result,
                    "health_contract": method_name,
                }
        stats_method = getattr(engine_obj, "get_stats", None)
        if callable(stats_method):
            try:
                result = stats_method()
            except Exception:
                result = None
            if isinstance(result, dict):
                return {
                    "contract_version": "kdo.observer.v1",
                    "status": "unknown",
                    "observed_at": result.get("observed_at") or result.get("timestamp"),
                    "metrics": result,
                    "health_contract": "stats_only",
                }
        return {
            "contract_version": "kdo.observer.v1",
            "status": "unknown",
            "metrics": {"engine": engine_name, "health_contract": "missing"},
            "health_contract": "missing",
        }
    return observe


ENGINE_FACTORY_GETTERS = frozenset({
    ("services.job_stats_service", "get_job_stats_service"),
    ("core.render_queue", "get_render_queue"),
})


def auto_discover_engines(director: "Director", container) -> int:
    discovered = 0
    report = {
        "registry_entries": len(ENGINE_REGISTRY),
        "discovered": [],
        "missing_exports": [],
        "import_failures": [],
        "factory_failures": [],
        "unsupported_exports": [],
        "plugin_observers": [],
    }
    for module_path, attr_name, display_name, sector in ENGINE_REGISTRY:
        try:
            module = __import__(module_path, fromlist=[attr_name])
            engine = getattr(module, attr_name, None)
            if engine is None:
                report["missing_exports"].append({
                    "observer": display_name, "module": module_path, "export": attr_name,
                })
                continue
            if (module_path, attr_name) in ENGINE_FACTORY_GETTERS:
                if not callable(engine):
                    report["factory_failures"].append({
                        "observer": display_name, "reason": "configured_factory_not_callable",
                    })
                    log.warning("Director: configured factory %s.%s is not callable", module_path, attr_name)
                    continue
                try:
                    engine = engine()
                except Exception as exc:
                    report["factory_failures"].append({
                        "observer": display_name, "reason": sanitize_error_message(exc),
                    })
                    log.warning("Director: factory %s failed: %s", display_name, sanitize_error_message(exc))
                    continue
            elif isinstance(engine, type):
                get_existing = getattr(container, "try_get_instance", None)
                live_instance = get_existing(engine) if callable(get_existing) else None
                if live_instance is None:
                    report["unsupported_exports"].append({
                        "observer": display_name,
                        "module": module_path,
                        "export": attr_name,
                        "kind": "class",
                        "reason": "live_instance_or_explicit_adapter_required",
                    })
                    continue
                engine = live_instance
            elif callable(engine):
                report["unsupported_exports"].append({
                    "observer": display_name,
                    "module": module_path,
                    "export": attr_name,
                    "kind": "callable",
                    "reason": "live_instance_or_explicit_adapter_required",
                })
                continue
            director.register_observer(
                FunctionObserver(display_name, sector, _make_observer(display_name, engine))
            )
            report["discovered"].append({
                "observer": display_name, "module": module_path, "export": attr_name,
                "sector": getattr(sector, "value", str(sector)),
            })
            discovered += 1
        except ImportError as exc:
            report["import_failures"].append({
                "observer": display_name, "module": module_path,
                "reason": sanitize_error_message(exc),
            })
            log.debug("Director: %s not available", display_name)
        except Exception as exc:
            report["import_failures"].append({
                "observer": display_name, "module": module_path,
                "reason": sanitize_error_message(exc),
            })
            log.warning("Director: %s failed: %s", display_name, sanitize_error_message(exc))

    plugins = getattr(container, "industry_plugins", {}) or {}
    if isinstance(plugins, dict):
        for name, plugin in plugins.items():
            sector = _infer_plugin_sector(name)
            director.register_observer(
                FunctionObserver(name, sector, _make_observer(name, plugin))
            )
            report["plugin_observers"].append(str(name))
            discovered += 1

    report["discovered_count"] = discovered
    report["missing_export_count"] = len(report["missing_exports"])
    report["import_failure_count"] = len(report["import_failures"])
    report["factory_failure_count"] = len(report["factory_failures"])
    report["unsupported_export_count"] = len(report["unsupported_exports"])
    director.engine_discovery_report = report
    log.info(
        "Director: Auto-discovered %d engines (%d missing exports, %d unsupported exports, %d import failures, %d factory failures)",
        discovered, report["missing_export_count"], report["unsupported_export_count"], report["import_failure_count"], report["factory_failure_count"],
    )
    return discovered


def _infer_plugin_sector(name: str) -> Sector:
    name_lower = name.lower()
    if any(kw in name_lower for kw in ("church", "school")):
        return Sector.BRANDS
    if any(kw in name_lower for kw in ("hospital", "clinic", "health")):
        return Sector.HEALTH
    if any(kw in name_lower for kw in ("crm", "client", "customer")):
        return Sector.CLIENTS
    return Sector.INFRA


# ═══════════════════════════════════════════════════════════════════════════
# THE DIRECTOR
# ═══════════════════════════════════════════════════════════════════════════

class Director:
    """The Autonomous CEO — v5.5 with proposals, detectors, verified outcomes."""

    VERSION = "5.5.0"
    MAX_TASKS = 1000
    MAX_CONCURRENT_EXECUTIONS = 5
    DETECTOR_INTERVAL_SECONDS = 6 * 3600

    def __init__(self, supabase_client=None, interval: int = 60, strategic_planner=None, platform_state_reader=None):
        self.store = AsyncSupabaseStore(supabase_client) if supabase_client else InMemoryStore()
        log.info("Director storage: %s", "Supabase" if supabase_client else "In-Memory")

        self.interval = max(10, interval)
        self.policy = Policy()
        self.running = False
        self.started = now()

        self._loop_task: Optional[asyncio.Task] = None
        self._event_task: Optional[asyncio.Task] = None
        self._execution_semaphore = asyncio.Semaphore(self.MAX_CONCURRENT_EXECUTIONS)
        self._executing: set = set()
        self._lock = asyncio.Lock()

        self.observers: Dict[str, FunctionObserver] = {}
        self.engine_discovery_report: dict = {
            "registry_entries": len(ENGINE_REGISTRY),
            "discovered": [],
            "missing_exports": [],
            "import_failures": [],
            "factory_failures": [],
            "unsupported_exports": [],
            "plugin_observers": [],
            "discovered_count": 0,
            "missing_export_count": 0,
            "import_failure_count": 0,
            "factory_failure_count": 0,
            "unsupported_export_count": 0,
        }
        self.capabilities: Dict[str, Capability] = {}
        self.capability_binding_report: Dict[str, str] = {}
        self.capability_binding_at: Optional[str] = None
        self._authority_fence = None
        self.tasks: Dict[str, Task] = {}
        self.goals: Dict[str, Goal] = {}

        self.history: deque = deque(maxlen=5000)
        self.decisions: deque = deque(maxlen=2000)
        self.observations: Dict[str, Observation] = {}
        self.briefings: deque = deque(maxlen=200)
        self._alert_history: deque = deque(maxlen=500)

        self._event_queue: deque = deque(maxlen=2000)
        self._event_callback: Optional[Callable] = None
        self._alert_callback: Optional[Callable] = None

        self.last_plan: Optional[datetime] = None
        self.strategic_planner = strategic_planner
        self.strategic_plans: Dict[str, dict] = {}
        self._platform_state_reader = platform_state_reader
        if self._platform_state_reader is None and supabase_client is not None:
            try:
                from core.platform_state_reader import PlatformStateReader
                self._platform_state_reader = PlatformStateReader(supabase_client)
            except Exception:
                log.warning("Director Platform State reader could not be initialized; shared state remains unknown")
        self._platform_state_context: Dict[str, Any] = {
            "status": "unavailable",
            "freshness": "unknown",
            "revision": 0,
            "services": [],
            "capabilities": [],
            "reason": "platform_state_reader_not_configured",
        }
        self.last_observation: Optional[datetime] = None
        self._last_daily_briefing: Optional[str] = None
        self._consecutive_failures: int = 0
        self._observation_circuit_open: bool = False
        self._observation_circuit_reset_time: float = 0.0

        self.topology = SystemTopology()
        self.root_cause_analyzer = RootCauseAnalyzer(self.topology)
        self.impact_assessor = ImpactAssessor(self.topology)
        self.cascading_predictor = CascadingFailurePredictor(self.topology)
        self.recovery_coordinator = CorrelatedRecovery()

        self.learning_loop = LearningLoop()
        self.adaptive_thresholds = AdaptiveThresholds()

        self.current_root_cause: Optional[RootCauseAnalysis] = None
        self.last_diagnosis_evidence: Dict[str, Any] = {
            "max_age_seconds": 300, "input_count": 0,
            "accepted_observers": [], "rejected": [], "conflicting_sectors": [],
        }
        self.current_impact: Dict[str, Any] = {}
        self.current_predictions: List[Dict[str, Any]] = []

        self.council = get_council() if HAS_COUNCIL else None
        if self.council:
            log.info("Director: Community Council activated (%d members)", len(self.council.members))

        self.proposals: Dict[str, Proposal] = {}
        self.signals: Dict[str, DirectorSignal] = {}
        self.proposal_generator = ProposalGenerator(self)
        self.detectors: List[Detector] = [
            EngagementByHourDetector(),
            LoadForecastDetector(),
        ]
        self._last_detector_run: Optional[datetime] = None

        self._init_goals()
        self._init_capabilities()
        log.info("👔 KANYOZA DIRECTOR v%s initialized — verified outcomes + proposals", self.VERSION)

    @property
    def service_name(self) -> str:
        return "director"

    async def refresh_platform_state_context(self, max_age_seconds: int = 300) -> dict:
        """Refresh a bounded, read-only Platform State view for planning.

        The snapshot is advisory context only. Missing, expired, or unreadable
        state is represented explicitly and never converted to healthy.
        """
        if isinstance(max_age_seconds, bool) or not isinstance(max_age_seconds, int) or max_age_seconds < 1:
            raise ValueError("max_age_seconds must be a positive integer")
        reader = self._platform_state_reader
        if reader is None or not callable(getattr(reader, "get_snapshot", None)):
            self._platform_state_context = {
                "status": "unavailable", "freshness": "unknown", "revision": 0,
                "services": [], "capabilities": [], "observer_observations": [],
                "reason": "platform_state_reader_not_configured",
            }
            return dict(self._platform_state_context)

        try:
            snapshot = await reader.get_snapshot()
        except Exception:
            log.warning("Director Platform State refresh failed closed", exc_info=True)
            self._platform_state_context = {
                "status": "unavailable", "freshness": "unknown", "revision": 0,
                "services": [], "capabilities": [], "observer_observations": [],
                "reason": "platform_state_read_failed",
            }
            return dict(self._platform_state_context)

        if snapshot is None:
            self._platform_state_context = {
                "status": "uninitialized", "freshness": "unknown", "revision": 0,
                "services": [], "capabilities": [], "observer_observations": [],
                "reason": "platform_state_snapshot_missing",
            }
            return dict(self._platform_state_context)

        updated_at = getattr(snapshot, "updated_at", None)
        now_utc = datetime.now(timezone.utc)
        if not isinstance(updated_at, datetime) or updated_at.tzinfo is None:
            freshness, status, reason = "invalid", "unknown", "platform_state_timestamp_invalid"
            age_seconds = None
        else:
            age_seconds = (now_utc - updated_at.astimezone(timezone.utc)).total_seconds()
            if age_seconds < -30:
                freshness, status, reason = "invalid", "unknown", "platform_state_timestamp_in_future"
            elif age_seconds > max_age_seconds:
                freshness, status, reason = "stale", "stale", "platform_state_snapshot_expired"
            else:
                freshness, status, reason = "fresh", "available", None

        service_items = list(getattr(snapshot, "services", ()) or ())
        capability_items = list(getattr(snapshot, "capabilities", ()) or ())
        readiness_counts: Dict[str, int] = {}
        for item in service_items:
            readiness = getattr(getattr(item, "readiness", None), "value", str(getattr(item, "readiness", "unknown")))
            readiness_counts[readiness] = readiness_counts.get(readiness, 0) + 1

        services = []
        for item in service_items[:100]:
            evidence = []
            for ref in list(getattr(item, "evidence", ()) or ())[:3]:
                observed = getattr(ref, "observed_at", None)
                evidence.append({
                    "source": str(getattr(ref, "source", "unknown"))[:100],
                    "source_version": str(getattr(ref, "source_version", "unknown"))[:80],
                    "observed_at": observed.isoformat() if isinstance(observed, datetime) else None,
                    "correlation_id": getattr(ref, "correlation_id", None),
                    "reference": getattr(ref, "reference", None),
                })
            observed = getattr(item, "observed_at", None)
            reported_readiness = getattr(getattr(item, "readiness", None), "value", str(getattr(item, "readiness", "unknown")))
            effective_readiness = reported_readiness if freshness == "fresh" else ("stale" if freshness == "stale" else "unknown")
            services.append({
                "service_id": str(getattr(item, "service_id", "unknown"))[:120],
                "owner": str(getattr(item, "owner", "unknown"))[:120],
                "readiness": effective_readiness,
                "reported_readiness": reported_readiness,
                "freshness": freshness,
                "observed_at": observed.isoformat() if isinstance(observed, datetime) else None,
                "reason": str(getattr(item, "reason", "") or "")[:200] or None,
                "critical": bool(getattr(item, "critical", False)),
                "evidence": evidence,
            })

        capabilities = []
        for item in capability_items[:100]:
            observed = getattr(item, "observed_at", None)
            reported_readiness = getattr(getattr(item, "readiness", None), "value", str(getattr(item, "readiness", "unknown")))
            effective_readiness = reported_readiness if freshness == "fresh" else ("stale" if freshness == "stale" else "unknown")
            capabilities.append({
                "capability_id": str(getattr(item, "capability_id", "unknown"))[:120],
                "owner_service": str(getattr(item, "owner_service", "unknown"))[:120],
                "readiness": effective_readiness,
                "reported_readiness": reported_readiness,
                "freshness": freshness,
                "observed_at": observed.isoformat() if isinstance(observed, datetime) else None,
                "reason": str(getattr(item, "reason", "") or "")[:200] or None,
            })

        observer_items = list(getattr(snapshot, "observer_observations", ()) or ())
        observer_observations = []
        for item in observer_items[:100]:
            observed = getattr(item, "observed_at", None)
            reported_readiness = getattr(
                getattr(item, "readiness", None),
                "value",
                str(getattr(item, "readiness", "unknown")),
            )
            observer_freshness = freshness
            if freshness == "fresh":
                if not isinstance(observed, datetime) or observed.tzinfo is None:
                    observer_freshness = "unknown"
                else:
                    observer_age = (
                        now_utc - observed.astimezone(timezone.utc)
                    ).total_seconds()
                    if observer_age < -30:
                        observer_freshness = "unknown"
                    elif observer_age > max_age_seconds:
                        observer_freshness = "stale"
            effective_readiness = (
                reported_readiness if observer_freshness == "fresh"
                else ("stale" if observer_freshness == "stale" else "unknown")
            )
            evidence = []
            for ref in list(getattr(item, "evidence", ()) or ())[:2]:
                evidence.append({
                    "source": str(getattr(ref, "source", "unknown"))[:100],
                    "source_version": str(getattr(ref, "source_version", "unknown"))[:80],
                })
            observer_observations.append({
                "service_id": str(getattr(item, "service_id", "unknown"))[:120],
                "observer_id": str(getattr(item, "observer_id", "unknown"))[:80],
                "readiness": effective_readiness,
                "reported_readiness": reported_readiness,
                "freshness": observer_freshness,
                "observed_at": observed.isoformat() if isinstance(observed, datetime) else None,
                "source_version": str(getattr(item, "source_version", "unknown"))[:80],
                "correlation_id": getattr(item, "correlation_id", None),
                "evidence": evidence,
            })

        self._platform_state_context = {
            "status": status,
            "freshness": freshness,
            "revision": int(getattr(snapshot, "revision", 0)),
            "schema_version": int(getattr(snapshot, "schema_version", 1)),
            "updated_at": updated_at.isoformat() if isinstance(updated_at, datetime) else None,
            "age_seconds": round(age_seconds, 2) if age_seconds is not None else None,
            "service_count": len(service_items),
            "capability_count": len(capability_items),
            "readiness_counts": readiness_counts,
            "services": services,
            "capabilities": capabilities,
            "observer_observations": observer_observations,
            "observer_observation_count": len(observer_items),
            "observer_observations_truncated": len(observer_items) > len(observer_observations),
            "services_truncated": len(service_items) > len(services),
            "capabilities_truncated": len(capability_items) > len(capabilities),
            "reason": reason,
            "policy": {
                "read_only": True,
                "unknown_is_not_healthy": True,
                "grants_execution_authority": False,
                "sensitive_actions_require_existing_governance": True,
            },
        }
        return dict(self._platform_state_context)

    async def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.started = now()
        self._loop_task = asyncio.create_task(self._main_loop(), name="director-loop")
        self._event_task = asyncio.create_task(self._event_drain_loop(), name="director-events")
        asyncio.create_task(self._startup())
        log.info("👔 Director v%s ONLINE", self.VERSION)

    async def stop(self) -> None:
        if not self.running:
            return
        log.info("Stopping Director...")
        self.running = False
        for task in [self._loop_task, self._event_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._loop_task = None
        self._event_task = None
        timed_out_publications = await flush_pending_event_tasks(timeout_seconds=2.0)
        if timed_out_publications:
            log.warning(
                "Director shutdown cancelled %d event publication task(s) after drain timeout",
                timed_out_publications,
            )
        log.info("👔 Director OFFLINE")

    async def check_health_async(self) -> Dict[str, Any]:
        health = self.health()
        return {
            "status": "healthy" if self.running else "stopped",
            "score": health["score"],
            "version": self.VERSION,
            "observers": len(self.observers),
            "capabilities": len(self.capabilities),
            "pending_approvals": sum(1 for t in self.tasks.values() if t.status == Status.APPROVAL),
            "pending_proposals": sum(1 for p in self.proposals.values() if p.status == ProposalStatus.PENDING),
            "event_queue_depth": len(self._event_queue),
            "executing_tasks": len(self._executing),
            "topology_nodes": len(self.topology.nodes),
            "learning_signals": len(self.learning_loop.signals),
        }

    async def _main_loop(self) -> None:
        last_minute = None
        while self.running:
            if self._observation_circuit_open:
                if time.monotonic() > self._observation_circuit_reset_time:
                    self._observation_circuit_open = False
                    self._consecutive_failures = 0
                    log.info("Director: Observation circuit breaker reset")
                else:
                    await asyncio.sleep(self.interval)
                    continue
            try:
                await self._observe_async()
                self._consecutive_failures = 0
                if self.last_plan is None or (now() - self.last_plan).total_seconds() >= 1800:
                    await self._build_plan_async()
                await self._execute_due_async()

                if (
                    self._last_detector_run is None
                    or (now() - self._last_detector_run).total_seconds() >= self.DETECTOR_INTERVAL_SECONDS
                ):
                    self._last_detector_run = now()
                    await self._run_detectors()

                current_minute = now().strftime("%Y-%m-%d-%H-%M")
                if current_minute != last_minute:
                    last_minute = current_minute
                    await self._check_briefings()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._consecutive_failures += 1
                log.exception(
                    "Director loop error (%d consecutive): %s",
                    self._consecutive_failures,
                    sanitize_error_message(exc),
                )
                if self._consecutive_failures >= 3:
                    backoff = min(300, 30 * self._consecutive_failures)
                    self._observation_circuit_open = True
                    self._observation_circuit_reset_time = time.monotonic() + backoff
                    log.critical("Director: Circuit breaker OPEN — backing off for %ds", backoff)
            await asyncio.sleep(self.interval)

    async def _event_drain_loop(self) -> None:
        while self.running:
            self._drain_events()
            await asyncio.sleep(15)
        self._drain_events()

    async def _startup(self) -> None:
        await asyncio.sleep(2)
        try:
            await self._restore_decisions()
            await self._restore_tasks()
            await self._replay_learning_history()
            await self._observe_async()
            await self._build_plan_async()
            await self._execute_due_async()
            self.briefing(Briefing.CHECKPOINT)
        except Exception as exc:
            log.exception("Startup failed: %s", sanitize_error_message(exc))

    @staticmethod
    def _is_process_global_decision_record(raw: dict) -> bool:
        """Exclude workspace-scoped integration audit records from global state.

        Legacy website-event decisions predate the explicit record_scope marker;
        identify those by their observation source as well.
        """
        if raw.get("record_scope") == "workspace":
            return False
        if raw.get("director_global_restore") is False:
            return False
        observed_state = raw.get("observed_state")
        if isinstance(observed_state, list) and any(
            isinstance(item, dict)
            and item.get("source") == "website_integration_events"
            for item in observed_state
        ):
            return False
        return True

    async def _restore_decisions(self) -> dict:
        """Restore the bounded process-global decision audit trail."""
        try:
            records = await self.store.list_decisions(limit=2000)
        except Exception as exc:
            log.warning("Director decision restore failed: %s", sanitize_error_message(exc))
            return {"restored": 0, "errors": 1}

        restored = errors = 0
        for record in records or []:
            try:
                raw = record.get("payload", record) if isinstance(record, dict) else record
                if isinstance(raw, str):
                    import json
                    raw = json.loads(raw)
                if not isinstance(raw, dict) or not raw.get("id"):
                    errors += 1
                    continue
                if not self._is_process_global_decision_record(raw):
                    continue
                fields = {key: raw[key] for key in Decision.__dataclass_fields__ if key in raw}
                stamp = fields.get("timestamp")
                if isinstance(stamp, str) and stamp:
                    fields["timestamp"] = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                fields["sector"] = Sector(fields.get("sector", Sector.SELF.value))
                fields["authority"] = Authority(fields.get("authority", Authority.SUGGEST.value))
                fields["risk"] = Risk(fields.get("risk", Risk.MEDIUM.value))
                self.decisions.append(Decision(**fields))
                restored += 1
            except Exception as exc:
                errors += 1
                log.warning("Skipping invalid persisted Director decision: %s", sanitize_error_message(exc))

        summary = {"restored": restored, "errors": errors}
        log.info("Director decision restore: %s", summary)
        return summary

    async def _restore_tasks(self) -> dict:
        try:
            records = await self.store.list_tasks()
        except Exception as exc:
            log.warning("Director task restore failed: %s", sanitize_error_message(exc))
            return {"restored": 0, "flagged_for_reconciliation": 0, "errors": 1}

        restored = flagged = errors = 0
        for record in records or []:
            try:
                raw = record.get("payload", record) if isinstance(record, dict) else record
                if isinstance(raw, str):
                    import json
                    raw = json.loads(raw)
                if not isinstance(raw, dict) or not raw.get("id"):
                    errors += 1
                    continue
                # Ownership lives in the row metadata as well as the task payload.
                # Carry it into the model so reconciliation writes cannot silently
                # promote legacy_unclassified records into platform state.
                if isinstance(record, dict) and record.get("scope_type"):
                    raw["scope_type"] = record["scope_type"]
                fields = {key: raw[key] for key in Task.__dataclass_fields__ if key in raw}
                fields["sector"] = Sector(fields.get("sector", Sector.SELF.value))
                fields["authority"] = Authority(fields.get("authority", Authority.SUGGEST.value))
                fields["risk"] = Risk(fields.get("risk", Risk.MEDIUM.value))
                fields["status"] = Status(fields.get("status", Status.PLANNED.value))
                for key in ("created_at", "executed_at", "approved_at", "evidence_observed_at"):
                    value = fields.get(key)
                    if isinstance(value, str) and value:
                        fields[key] = datetime.fromisoformat(value.replace("Z", "+00:00"))
                task = Task(**fields)
                if task.status == Status.EXECUTING:
                    task.status = Status.FLAGGED
                    task.error = "restart_reconciliation_required: execution outcome is ambiguous"
                    task.result = {
                        "status": "reconciliation_required",
                        "reason": "Director restarted while task was executing; automatic retry suppressed",
                    }
                    flagged += 1
                    await self.store.save_task(task)
                self.tasks[task.id] = task
                restored += 1
            except Exception as exc:
                errors += 1
                log.warning("Skipping invalid persisted Director task: %s", sanitize_error_message(exc))

        summary = {"restored": restored, "flagged_for_reconciliation": flagged, "errors": errors}
        log.info("Director task restore: %s", summary)
        return summary

    async def _replay_learning_history(self) -> None:
        lookback_days = 30
        since = now() - timedelta(days=lookback_days)
        try:
            signals = await self.store.list_learnings_since(since=since, limit=1000)
        except Exception as exc:
            log.warning(
                "Learning replay: failed to load signals: %s",
                sanitize_error_message(exc),
            )
            return
        if not signals:
            log.info("Learning replay: no historical signals within %d days", lookback_days)
            return
        try:
            replayed = self.learning_loop.rebuild_from_signals(signals)
            if replayed == len(signals):
                log.info(
                    "Learning replay: replayed %d signals (lookback %d days)",
                    replayed, lookback_days,
                )
            else:
                log.warning(
                    "Learning replay: fetched %d signals, replayed only %d "
                    "(lookback %d days) — %d skipped",
                    len(signals), replayed, lookback_days,
                    len(signals) - replayed,
                )
        except Exception as exc:
            log.exception(
                "Learning replay: rebuild failed: %s",
                sanitize_error_message(exc),
            )

    async def _check_briefings(self) -> None:
        hour = now().hour
        minute = now().minute
        if hour == 8 and minute == 0:
            today = now().strftime("%Y-%m-%d")
            if self._last_daily_briefing != today:
                self._last_daily_briefing = today
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._send_daily_briefing)
        if minute == 0 and hour in (6, 12, 18, 22):
            kind = (
                Briefing.MORNING if hour == 6
                else Briefing.EVENING if hour == 22
                else Briefing.CHECKPOINT
            )
            self.briefing(kind)

    async def _run_detectors(self) -> None:
        context = {
            "supabase_client": getattr(self.store, "supabase", None),
            "director": self,
            "since": now() - timedelta(days=30),
        }

        workspace_ids = await self._get_active_workspaces()
        if not workspace_ids:
            log.info("Director detector cycle skipped: no active database-backed workspaces")
            return

        for ws_id in workspace_ids:
            for detector in self.detectors:
                try:
                    signals = await detector.detect(ws_id, context)
                except Exception as exc:
                    log.warning(
                        "Detector %s failed for ws %s: %s",
                        detector.name, ws_id, sanitize_error_message(exc),
                    )
                    continue

                for signal in signals:
                    self.signals[signal.id] = signal
                    try:
                        proposal = self.proposal_generator.from_signal(signal)
                    except Exception as exc:
                        log.warning(
                            "Proposal generation failed for signal %s: %s",
                            signal.id, sanitize_error_message(exc),
                        )
                        continue
                    if proposal is not None:
                        self.proposals[proposal.id] = proposal
                        _publish_event("proposal_created", {
                            "proposal_id": proposal.id,
                            "title": proposal.title,
                            "workspace_id": proposal.workspace_id,
                            "sector": proposal.sector.value,
                        })
                        log.info("Proposal created: %s", proposal.title)

    async def _get_active_workspaces(self) -> List[str]:
        supabase = getattr(self.store, "supabase", None)
        if supabase is None:
            return []
        try:
            resp = supabase.table("workspaces").select("id").eq("status", "active").execute()
            return [r["id"] for r in (resp.data or []) if r.get("id")]
        except Exception:
            return []

    def approve_proposal(self, proposal_id: str, actor: str = "admin") -> Task:
        proposal = self.proposals.get(proposal_id)
        if proposal is None:
            raise KeyError(f"Proposal {proposal_id} not found")
        if proposal.status != ProposalStatus.PENDING:
            raise ValueError(f"Proposal is {proposal.status.value}, cannot approve")
        if proposal.expires_at and now() > proposal.expires_at:
            proposal.status = ProposalStatus.EXPIRED
            raise ValueError("Proposal has expired")

        proposal.status = ProposalStatus.APPROVED
        proposal.approved_at = now()
        proposal.approved_by = actor

        task = self._create_task(
            sector=proposal.sector,
            desc=proposal.title,
            cap_name=proposal.capability,
            reason=f"Proposal {proposal.id}: {proposal.expected_benefit}",
            confidence=proposal.confidence,
            params=proposal.params,
            source=f"proposal:{proposal.id}",
        )
        task.proposal_id = proposal.id
        task.approved_by = actor
        task.approved_at = proposal.approved_at

        if task.status in (Status.APPROVAL, Status.PLANNED):
            task.status = Status.APPROVED

        proposal.executed_task_id = task.id
        _publish_event("proposal_approved", {
            "proposal_id": proposal.id,
            "task_id": task.id,
            "actor": actor,
        })
        return task

    def reject_proposal(self, proposal_id: str, actor: str = "admin", reason: str = "") -> Proposal:
        proposal = self.proposals.get(proposal_id)
        if proposal is None:
            raise KeyError(f"Proposal {proposal_id} not found")
        if proposal.status != ProposalStatus.PENDING:
            raise ValueError(f"Proposal is {proposal.status.value}, cannot reject")
        proposal.status = ProposalStatus.REJECTED
        proposal.rejected_reason = reason or f"rejected by {actor}"
        _publish_event("proposal_rejected", {
            "proposal_id": proposal.id,
            "actor": actor,
            "reason": proposal.rejected_reason,
        })
        return proposal

    async def _observe_async(self) -> Dict[str, Observation]:
        loop = asyncio.get_event_loop()
        new_observations = {}
        tasks = []
        for name, observer in self.observers.items():
            task = loop.run_in_executor(None, observer.observe)
            tasks.append((name, task))
        for name, task in tasks:
            try:
                obs = await task
                new_observations[name] = obs
                await self.store.save_observation(obs)
            except Exception as exc:
                new_observations[name] = Observation(
                    name, Sector.INFRA, Health.DEGRADED,
                    issues=[{"type": "observer_error", "error": sanitize_error_message(exc)}],
                )
        async with self._lock:
            self.observations = new_observations
        self.last_observation = now()
        for name, obs in new_observations.items():
            self.topology.update_health(name, obs.status)
        self._run_diagnosis(new_observations)
        return self.observations

    def _snapshot_observations(self) -> Dict[str, Dict[str, Any]]:
        return {
            name: {
                "source": obs.source,
                "status": obs.status.value if hasattr(obs.status, "value") else str(obs.status),
                "observed_at": iso(obs.timestamp),
                "metrics": dict(obs.metrics or {}),
                "issues": list(obs.issues or []),
            }
            for name, obs in self.observations.items()
        }

    def _trusted_diagnosis_observations(
        self, observations: Dict[str, Observation], max_age_seconds: int = 300,
    ) -> tuple[Dict[str, Observation], dict]:
        current = now()
        fresh_known: Dict[str, Observation] = {}
        rejected = []
        sector_statuses: Dict[str, set] = defaultdict(set)
        sector_members: Dict[str, list] = defaultdict(list)

        for name, obs in observations.items():
            stamp = getattr(obs, "timestamp", None)
            if not isinstance(stamp, datetime) or not getattr(obs, "timestamp_valid", True):
                rejected.append({"observer": name, "reason": "invalid_timestamp"})
                continue
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            age = (current - stamp.astimezone(timezone.utc)).total_seconds()
            if age < -5 or age > max(1, int(max_age_seconds)):
                rejected.append({"observer": name, "reason": "observation_not_fresh", "age_seconds": round(age, 1)})
                continue
            status = getattr(obs, "status", Health.UNKNOWN)
            status_value = status.value if hasattr(status, "value") else str(status)
            if status_value == Health.UNKNOWN.value:
                rejected.append({"observer": name, "reason": "source_status_unknown"})
                continue
            sector_value = getattr(getattr(obs, "sector", None), "value", str(getattr(obs, "sector", "unknown")))
            fresh_known[name] = obs
            sector_statuses[sector_value].add(status_value)
            sector_members[sector_value].append(name)

        conflicts = sorted(sector for sector, statuses in sector_statuses.items() if len(statuses) > 1)
        for sector in conflicts:
            for name in sector_members[sector]:
                fresh_known.pop(name, None)
                rejected.append({"observer": name, "reason": "sector_status_conflict", "sector": sector})

        return fresh_known, {
            "max_age_seconds": max(1, int(max_age_seconds)),
            "input_count": len(observations),
            "accepted_observers": sorted(fresh_known),
            "rejected": rejected,
            "conflicting_sectors": conflicts,
        }

    def _run_diagnosis(self, observations: Dict[str, Observation]) -> None:
        trusted_observations, evidence_gate = self._trusted_diagnosis_observations(observations)
        self.last_diagnosis_evidence = evidence_gate
        if not trusted_observations:
            log.info("Diagnosis skipped: no fresh, known, non-conflicting evidence")
            return
        root_cause = self.root_cause_analyzer.analyze(trusted_observations)
        if root_cause:
            self.current_root_cause = root_cause
            self.current_impact = self.impact_assessor.assess_impact(root_cause.root_cause)
            self.current_predictions = self.cascading_predictor.predict_cascading_failures(
                root_cause.root_cause, observations,
            )
            log.warning(
                "🔍 ROOT CAUSE: %s (affects %d, confidence %.0f%%)",
                root_cause.root_cause,
                len(root_cause.affected_services),
                root_cause.confidence * 100,
            )
            plan = self.recovery_coordinator.create_recovery_plan(
                root_cause.root_cause, root_cause.affected_services,
            )
            log.info("📋 RECOVERY PLAN: %s", plan["id"])
            self._create_task(
                Sector.HEALTH,
                f"Fix root cause: {root_cause.root_cause}",
                "restart_failed_service",
                f"Root cause affects {len(root_cause.affected_services)} services",
                0.95,
                {
                    "service_name": root_cause.root_cause,
                    "affected_services": root_cause.affected_services,
                    "recovery_plan_id": plan["id"],
                },
                root_cause.root_cause,
            )
            self._send_diagnostic_alert()

    def _send_diagnostic_alert(self) -> None:
        if not self.current_root_cause:
            return
        try:
            rca = self.current_root_cause
            impact = self.current_impact
            predictions = self.current_predictions
            parts = [
                "🔍 <b>DIAGNOSTIC ALERT</b>",
                "━━━━━━━━━━━━━━━━━━━",
                f"<b>Root Cause:</b> {rca.root_cause}",
                f"<b>Confidence:</b> {rca.confidence:.0%}",
                f"<b>Affected:</b> {len(rca.affected_services)} services",
                f"<b>Blast Radius:</b> {impact.get('blast_radius', 0)} services",
                f"<b>Severity:</b> {impact.get('severity', 'unknown').upper()}",
            ]
            if predictions:
                parts.append("")
                parts.append("<b>Predicted Cascading:</b>")
                for p in predictions[:3]:
                    parts.append(
                        f"  • {p['service']} → {p['predicted_status']} "
                        f"in {p['time_to_failure_minutes']}min"
                    )
            parts.append("")
            parts.append(f"<b>Action:</b> {rca.recommended_action}")
            parts.append("━━━━━━━━━━━━━━━━━━━")
            parts.append(f"<i>{now().strftime('%H:%M:%S UTC')}</i>")
            self._send_telegram("\n".join(parts))
        except Exception as exc:
            log.error("Diagnostic alert failed: %s", sanitize_error_message(exc))

    def plan_objective(self, objective: dict, planner=None) -> dict:
        """Create a validated advisory plan from a strategic objective.

        This explicit method is never called by the periodic health/decision loop.
        It does not create Director tasks, invoke capability handlers, or grant
        authority. A configured planner adapter is required.
        """
        generator = planner or self.strategic_planner
        if not callable(generator):
            return {
                "status": "unavailable",
                "reason": "strategic_planner_not_configured",
                "advisory_only": True,
                "authority": "none",
                "execution_started": False,
            }
        plan = build_strategic_plan(
            objective=objective,
            context=self.strategic_context(),
            generator=generator,
        )
        self.strategic_plans[plan["plan_id"]] = plan
        while len(self.strategic_plans) > 100:
            self.strategic_plans.pop(next(iter(self.strategic_plans)))
        return plan

    def get_strategic_plan(self, plan_id: str) -> Optional[dict]:
        """Return a process-local advisory plan snapshot, if still retained."""
        plan = self.strategic_plans.get(str(plan_id))
        return dict(plan) if plan is not None else None

    async def create_strategic_plan(self, objective: dict, planner=None) -> dict:
        """Create and persist a validated advisory plan; never execute it."""
        await self.refresh_platform_state_context()
        plan = await asyncio.to_thread(self.plan_objective, objective, planner)
        if plan.get("status") == "unavailable":
            return plan
        saved = await self.store.save_strategic_plan(plan)
        if not saved:
            self.strategic_plans.pop(plan["plan_id"], None)
            raise RuntimeError("strategic_plan_persistence_unavailable")
        return plan

    async def load_strategic_plan(self, plan_id: str) -> Optional[dict]:
        """Load a plan from durable storage, falling back to the bounded cache."""
        plan = await self.store.get_strategic_plan(str(plan_id))
        if plan is not None:
            self.strategic_plans[plan_id] = plan
            return plan
        return self.strategic_plans.get(plan_id)

    async def list_strategic_plans(self, limit: int = 50) -> list[dict]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        return await self.store.list_strategic_plans(limit=limit)

    async def stage_strategic_mission(
        self, *, plan_id: str, workspace_id: str, idempotency_key: str,
        step_parameters: Dict[str, Dict[str, Any]], actor: str,
    ) -> dict:
        """Stage an operator-parameterized mission; never execute it."""
        workspace_id = str(workspace_id or "").strip()
        idempotency_key = str(idempotency_key or "").strip()
        if not workspace_id or not idempotency_key or len(idempotency_key) > 128:
            raise ValueError("workspace_and_bounded_idempotency_key_required")
        if not isinstance(step_parameters, dict) or not step_parameters:
            raise ValueError("step_parameters_required")

        plan = await self.load_strategic_plan(plan_id)
        if not isinstance(plan, dict):
            raise KeyError("strategic_plan_not_found")
        verification = await self.verify_strategic_plan(plan, max_age_seconds=300)
        if verification.get("verdict") != "verified_for_governance_review":
            raise ValueError("strategic_plan_requires_reverification:" + str(verification.get("verdict")))

        steps = plan.get("steps")
        if not isinstance(steps, list) or not 1 <= len(steps) <= 12:
            raise ValueError("strategic_plan_step_count_invalid")
        step_ids = [str(step.get("step_id") or "") for step in steps if isinstance(step, dict)]
        if len(step_ids) != len(steps) or len(set(step_ids)) != len(step_ids):
            raise ValueError("strategic_plan_step_ids_invalid")
        if set(step_parameters) != set(step_ids):
            raise ValueError("step_parameters_must_match_all_plan_steps")

        catalog = self.capability_catalog()
        for step in steps:
            step_id = str(step["step_id"])
            capability_name = str(step.get("capability") or "")
            cap = self.capabilities.get(capability_name)
            item = catalog.get(capability_name) or {}
            if cap is None or not item.get("mission_staging_ready"):
                raise ValueError("capability_not_mission_stageable:" + capability_name)
            if not item.get("runtime_binding_confirmed"):
                raise ValueError("capability_runtime_binding_unconfirmed:" + capability_name)
            if item.get("binding_status") not in {"bound", "bound_governed_handoff"}:
                raise ValueError("capability_binding_not_confirmed:" + capability_name)
            if (
                cap.risk != Risk.LOW
                or cap.proposal_capable
                or getattr(cap, "mission_staging_mode", "disabled") != "read_only_review"
                or cap.target_param != "brand_id"
            ):
                raise ValueError("capability_risk_or_execution_path_not_allowed:" + capability_name)
            params = step_parameters.get(step_id)
            errors = self.validate_capability_parameters(cap.parameter_schema, params)
            if errors:
                raise ValueError("invalid_step_parameters:" + step_id + ":" + ",".join(errors))
            brand_id = params.get("brand_id") if isinstance(params, dict) else None
            if brand_id:
                ownership_check = getattr(self.store, "brand_belongs_to_workspace", None)
                if not callable(ownership_check):
                    raise RuntimeError("durable_workspace_brand_validation_unavailable")
                belongs = await ownership_check(str(brand_id), workspace_id)
                if not belongs:
                    raise ValueError("brand_not_found_in_workspace:" + step_id)

        step_to_task = {
            step_id: "dir_mis_" + hashlib.sha256(
                (workspace_id + "|" + idempotency_key + "|" + step_id).encode("utf-8")
            ).hexdigest()[:20]
            for step_id in step_ids
        }
        mission_id = "mission_" + hashlib.sha256(
            (workspace_id + "|" + idempotency_key).encode("utf-8")
        ).hexdigest()[:20]
        created_at = now()
        try:
            observed_at = datetime.fromisoformat(
                str(plan.get("source_context_generated_at") or "").replace("Z", "+00:00")
            )
        except (TypeError, ValueError):
            observed_at = None

        tasks: list[Task] = []
        persisted_rows: list[dict] = []
        for step in steps:
            step_id = str(step["step_id"])
            cap = self.capabilities[str(step["capability"])]
            dependencies = [step_to_task[str(dep)] for dep in (step.get("depends_on") or [])]
            task = Task(
                id=step_to_task[step_id],
                sector=cap.sector,
                description=(str(step.get("objective") or step.get("target") or step_id)[:500]),
                capability=cap.name,
                authority=cap.authority,
                risk=cap.risk,
                params=dict(step_parameters[step_id]),
                status=Status.APPROVAL,
                created_at=created_at,
                confidence=0.8,
                reason=("Operator-staged from strategic plan " + str(plan_id) +
                        "; " + str(step.get("rationale") or "Review plan evidence before approval"))[:1000],
                sources=["strategic_plan:" + str(plan_id), "operator:" + str(actor or "operator")[:100]],
                workspace_id=workspace_id,
                depends_on=dependencies,
                mission_id=mission_id,
                success_criteria=list(step.get("acceptance_criteria") or []),
                recovery_policy="require_review",
                evidence_observed_at=observed_at,
                condition_fingerprint=str(plan.get("source_context_fingerprint") or ""),
            )
            tasks.append(task)
            persisted_rows.append({
                "id": task.id,
                "workspace_id": workspace_id,
                "status": task.status.value,
                "created_at": iso(task.created_at),
                "payload": safe(asdict(task)),
            })

        request_fingerprint = hashlib.sha256(json.dumps({
            "workspace_id": workspace_id,
            "plan_id": plan_id,
            "plan_fingerprint": plan.get("source_context_fingerprint"),
            "step_parameters": step_parameters,
            "task_ids": [task.id for task in tasks],
        }, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()
        persist = getattr(self.store, "stage_mission_admission", None)
        if not callable(persist):
            raise RuntimeError("durable_mission_admission_store_unavailable")
        receipt = await persist(
            workspace_id=workspace_id,
            idempotency_key=idempotency_key,
            plan_id=str(plan_id),
            mission_id=mission_id,
            request_fingerprint=request_fingerprint,
            tasks=persisted_rows,
        )
        if not isinstance(receipt, dict):
            raise RuntimeError("mission_admission_persistence_unavailable")
        if receipt.get("error") == "mission_idempotency_conflict":
            raise ValueError("mission_idempotency_conflict")
        if receipt.get("mission_id") != mission_id or receipt.get("task_ids") != [task.id for task in tasks]:
            raise RuntimeError("mission_admission_receipt_mismatch")

        for task in tasks:
            self.tasks[task.id] = task
            _publish_event("task_created", {
                "task_id": task.id,
                "mission_id": mission_id,
                "capability": task.capability,
                "workspace_id": workspace_id,
                "status": task.status.value,
                "source": "strategic_plan_admission",
            })

        return {
            "status": "staged_for_approval",
            "plan_id": str(plan_id),
            "mission_id": mission_id,
            "workspace_id": workspace_id,
            "task_ids": [task.id for task in tasks],
            "tasks": [safe(asdict(task)) for task in tasks],
            "replayed": bool(receipt.get("replayed")),
            "staged_by": str(actor or "operator")[:100],
            "authority": "none",
            "execution_permitted": False,
            "approval_required": True,
            "governance_required": True,
        }

    async def verify_strategic_plan(self, plan: dict, max_age_seconds: int = 300) -> dict:
        """Verify a stored advisory plan against freshly read Platform State and context."""
        await self.refresh_platform_state_context(max_age_seconds=max_age_seconds)
        context = self.strategic_context(max_age_seconds=max_age_seconds)
        return verify_plan_contract(
            plan, context, max_age_seconds=max_age_seconds
        )

    async def _build_plan_async(self) -> dict:
        await self.refresh_platform_state_context()
        tasks = self.decide()
        self.last_plan = now()
        strategic_context = self.strategic_context()
        return {
            "date": now().date().isoformat(),
            "decision_context": strategic_context["evidence"],
            "strategic_context": strategic_context,
            "tasks": [safe(asdict(t)) for t in tasks],
        }

    async def add_task_dependency(self, task_id: str, dependency_id: str) -> dict:
        if task_id == dependency_id:
            raise ValueError("a task cannot depend on itself")
        task = self.tasks.get(task_id)
        dependency = self.tasks.get(dependency_id)
        if task is None or dependency is None:
            raise KeyError("task and dependency must both exist")
        if task.status not in (Status.PLANNED, Status.APPROVAL, Status.APPROVED):
            raise ValueError("dependencies can only be changed before execution")
        if dependency_id in (getattr(task, "depends_on", []) or []):
            return {"task_id": task_id, "depends_on": list(task.depends_on), "changed": False}
        task_mission = getattr(task, "mission_id", None)
        dependency_mission = getattr(dependency, "mission_id", None)
        if task_mission and dependency_mission and task_mission != dependency_mission:
            raise ValueError("tasks already belong to different missions")

        stack, visited = [dependency_id], set()
        while stack:
            current = stack.pop()
            if current == task_id:
                raise ValueError("dependency would create a cycle")
            if current in visited:
                continue
            visited.add(current)
            current_task = self.tasks.get(current)
            if current_task is not None:
                stack.extend(getattr(current_task, "depends_on", []) or [])
        task.depends_on.append(dependency_id)
        if task.mission_id is None:
            task.mission_id = dependency.mission_id or f"mission_{uuid.uuid4().hex[:12]}"
        if dependency.mission_id is None:
            dependency.mission_id = task.mission_id
        await self.store.save_task(task)
        await self.store.save_task(dependency)
        return {"task_id": task_id, "depends_on": list(task.depends_on), "mission_id": task.mission_id, "changed": True}

    async def evaluate_task_success_criteria(self, task_id: str, max_age_seconds: int = 300) -> dict:
        if isinstance(max_age_seconds, bool) or not isinstance(max_age_seconds, int) or not 1 <= max_age_seconds <= 86400:
            raise ValueError("max_age_seconds must be an integer between 1 and 86400")
        task = self.tasks.get(task_id)
        if task is None:
            raise KeyError("task not found")
        criteria = list(getattr(task, "success_criteria", []) or [])
        if not criteria:
            return {"task_id": task_id, "outcome": "unknown", "reason": "no_success_criteria", "criteria": []}

        results = []
        unknown = False
        failed = False
        for index, criterion in enumerate(criteria):
            if not isinstance(criterion, dict):
                unknown = True
                results.append({"index": index, "outcome": "unknown", "reason": "free_text_criterion_not_machine_evaluable"})
                continue
            observer_name = criterion.get("observer")
            metric_path = criterion.get("metric_path")
            operator = criterion.get("operator", "equals")
            if (not isinstance(observer_name, str) or not observer_name.strip()
                    or not isinstance(metric_path, str) or not metric_path.strip()
                    or operator not in {"equals", "not_equals", "gt", "gte", "lt", "lte", "contains", "exists"}
                    or (operator != "exists" and "expected" not in criterion)):
                unknown = True
                results.append({"index": index, "outcome": "unknown", "reason": "invalid_structured_criterion"})
                continue
            obs = self.observations.get(observer_name.strip())
            if obs is None:
                unknown = True
                results.append({"index": index, "outcome": "unknown", "reason": "observer_missing", "observer": observer_name})
                continue
            stamp = getattr(obs, "timestamp", None)
            if not isinstance(stamp, datetime):
                unknown = True
                results.append({"index": index, "outcome": "unknown", "reason": "invalid_observation_timestamp", "observer": observer_name})
                continue
            stamp_utc = stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)
            age = (datetime.now(timezone.utc) - stamp_utc).total_seconds()
            status = getattr(getattr(obs, "status", None), "value", getattr(obs, "status", None))
            if age < -30 or age > max_age_seconds or str(status).lower() not in {"nominal", "healthy", "ok", "ready"}:
                unknown = True
                results.append({"index": index, "outcome": "unknown", "reason": "observer_not_fresh_and_healthy", "observer": observer_name, "age_seconds": round(age, 3), "reported_status": status})
                continue
            value: Any = getattr(obs, "metrics", {}) or {}
            path_parts = metric_path.split(".")
            try:
                for part in path_parts:
                    if not part or not isinstance(value, dict) or part not in value:
                        raise KeyError(part)
                    value = value[part]
            except KeyError:
                unknown = True
                results.append({"index": index, "outcome": "unknown", "reason": "metric_missing", "observer": observer_name, "metric_path": metric_path})
                continue
            expected = criterion.get("expected")
            try:
                if operator == "exists":
                    matched = value is not None
                elif operator == "equals":
                    matched = value == expected and type(value) is type(expected)
                elif operator == "not_equals":
                    matched = not (value == expected and type(value) is type(expected))
                elif operator == "gt": matched = value > expected
                elif operator == "gte": matched = value >= expected
                elif operator == "lt": matched = value < expected
                elif operator == "lte": matched = value <= expected
                else: matched = isinstance(value, (str, list, tuple, dict)) and expected in value
            except (TypeError, ValueError):
                unknown = True
                results.append({"index": index, "outcome": "unknown", "reason": "incompatible_metric_type", "observer": observer_name, "metric_path": metric_path})
                continue
            if not matched:
                failed = True
            results.append({"index": index, "outcome": "met" if matched else "not_met", "observer": observer_name, "metric_path": metric_path, "operator": operator, "observed_value": value, "expected": expected if operator != "exists" else None, "observed_at": iso(stamp)})

        outcome = "unknown" if unknown else ("ineffective" if failed else "verified")
        if task.status in (Status.COMPLETED, Status.VERIFIED, Status.INEFFECTIVE):
            if outcome == "verified":
                task.status = Status.VERIFIED
            elif outcome == "ineffective":
                task.status = Status.INEFFECTIVE
            if outcome != "unknown":
                prior = task.result if isinstance(task.result, dict) else {"handler_result": task.result}
                task.result = safe({**prior, "outcome_verification": {"outcome": outcome, "evaluated_at": iso(now()), "criteria": results}})
                try:
                    await self.store.save_task(task)
                except Exception as exc:
                    log.exception("Failed to persist task outcome reconciliation: %s", sanitize_error_message(exc))
                    return {"task_id": task_id, "outcome": outcome, "criteria": results, "task_status": task.status.value, "state_updated": True, "persistence": "failed"}
        return {"task_id": task_id, "outcome": outcome, "criteria": results, "task_status": task.status.value, "state_updated": task.status in (Status.VERIFIED, Status.INEFFECTIVE)}

    def mission_graph(self, mission_id: Optional[str] = None) -> dict:
        selected = {
            tid: task for tid, task in self.tasks.items()
            if mission_id is None or getattr(task, "mission_id", None) == mission_id
        }
        nodes = []
        edges = []
        for tid, task in selected.items():
            nodes.append({
                "task_id": tid, "mission_id": getattr(task, "mission_id", None),
                "capability": task.capability, "description": task.description,
                "status": task.status.value,
                "success_criteria": list(getattr(task, "success_criteria", []) or []),
                "recovery_policy": getattr(task, "recovery_policy", "require_review"),
                "impact_score": float(getattr(task, "impact_score", 50.0)),
                "mission_priority": float(getattr(task, "mission_priority", 50.0)),
            })
            for dep_id in getattr(task, "depends_on", []) or []:
                edges.append({"prerequisite": dep_id, "dependent": tid, "resolved": dep_id in self.tasks})
        return {"version": 1, "storage_scope": "current_memory_with_startup_restore", "mission_id": mission_id, "nodes": nodes, "edges": edges}

    def assess_task_recovery(self, task_id: str) -> dict:
        task = self.tasks.get(task_id)
        if task is None:
            raise KeyError("task not found")

        status = task.status.value if hasattr(task.status, "value") else str(task.status)
        dependencies = []
        missing_dependencies = []
        unsuccessful_dependencies = []
        pending_dependencies = []
        for dependency_id in list(getattr(task, "depends_on", []) or []):
            dependency = self.tasks.get(dependency_id)
            if dependency is None:
                missing_dependencies.append(dependency_id)
                dependencies.append({"task_id": dependency_id, "status": "missing"})
                continue
            dependency_status = dependency.status.value if hasattr(dependency.status, "value") else str(dependency.status)
            dependencies.append({"task_id": dependency_id, "status": dependency_status})
            if dependency.status in {Status.FAILED, Status.SKIPPED, Status.CANCELLED, Status.INEFFECTIVE, Status.BLOCKED}:
                unsuccessful_dependencies.append(dependency_id)
            elif dependency.status not in {Status.COMPLETED, Status.VERIFIED}:
                pending_dependencies.append(dependency_id)

        if status == Status.FLAGGED.value:
            category = "reconcile_interrupted_execution"
            steps = [
                "Inspect the execution ledger and any available receipt for this task.",
                "Query the authoritative domain observer to determine whether the intended side effect occurred.",
                "Do not retry until the prior attempt is reconciled and normal authorization is satisfied.",
            ]
        elif status == Status.FAILED.value:
            category = "review_failed_execution"
            steps = [
                "Inspect the sanitized error and execution receipt or ledger record.",
                "Check authoritative domain state for a partial or completed side effect.",
                "Only consider a new attempt after idempotency, authorization, and approval requirements are reviewed.",
            ]
        elif status == Status.INEFFECTIVE.value:
            category = "replan_after_ineffective_outcome"
            steps = [
                "Review the outcome-verification evidence and the criterion that was not met.",
                "Refresh relevant observations and reassess whether the original strategy remains valid.",
                "Create a revised plan through the normal planning and approval path; do not repeat automatically.",
            ]
        elif status == Status.BLOCKED.value:
            category = "resolve_dependencies"
            if missing_dependencies:
                steps = ["Repair or explicitly remove missing prerequisite references through the authorized mission-management workflow."]
            elif unsuccessful_dependencies:
                steps = [
                    "Review unsuccessful prerequisite tasks and reconcile their external outcomes.",
                    "Replan the dependent task or its prerequisites through the normal planning and approval path.",
                ]
            else:
                steps = ["Wait for prerequisite tasks to reach completed or verified state, then reassess this task."]
        elif status in {Status.COMPLETED.value, Status.VERIFIED.value}:
            category = "no_recovery_needed"
            steps = ["No recovery is indicated; use outcome verification when measurable success criteria are available."]
        elif status in {Status.CANCELLED.value, Status.SKIPPED.value}:
            category = "review_cancelled_task"
            steps = ["Confirm the cancellation or skip reason and create a new task only if the goal remains valid."]
        elif status in {Status.PLANNED.value, Status.APPROVAL.value, Status.APPROVED.value, Status.APPROVED_PENDING_EXECUTION.value}:
            category = "await_normal_execution_gates"
            steps = ["Continue through the existing scheduler, dependency checks, authorization, and approval gates."]
        elif status == Status.EXECUTING.value:
            category = "execution_in_progress"
            steps = ["Do not launch a duplicate attempt; wait for the current execution receipt or reconcile if execution becomes interrupted."]
        else:
            category = "manual_review"
            steps = ["Inspect task state and execution evidence before deciding on any recovery action."]

        if pending_dependencies and category == "resolve_dependencies":
            steps.append("Pending prerequisites: " + ", ".join(pending_dependencies))

        task_result = task.result if isinstance(task.result, dict) else {}
        verification = task_result.get("outcome_verification")
        verification = verification if isinstance(verification, dict) else {}
        observed_outcome = verification.get("outcome", "not_recorded")
        handler_acknowledged = task_result.get("ok") is True or task_result.get("status") == "executed"
        if status in {Status.FLAGGED.value, Status.FAILED.value, Status.INEFFECTIVE.value, Status.EXECUTING.value}:
            retry_safety = "unsafe_until_reconciled"
            reconciliation_required = True
        elif status == Status.BLOCKED.value:
            retry_safety = "blocked_by_dependencies"
            reconciliation_required = bool(unsuccessful_dependencies or missing_dependencies)
        elif status in {Status.COMPLETED.value, Status.VERIFIED.value}:
            retry_safety = "not_applicable_completed"
            reconciliation_required = False
        else:
            retry_safety = "normal_execution_gates_required"
            reconciliation_required = False

        if reconciliation_required and "Reconcile the previous attempt against authoritative domain state before any retry." not in steps:
            steps.append("Reconcile the previous attempt against authoritative domain state before any retry.")
        return {
            "task_id": task_id,
            "mission_id": getattr(task, "mission_id", None),
            "status": status,
            "recovery_category": category,
            "recovery_policy": getattr(task, "recovery_policy", "require_review"),
            "dependencies": dependencies,
            "missing_dependencies": missing_dependencies,
            "unsuccessful_dependencies": unsuccessful_dependencies,
            "pending_dependencies": pending_dependencies,
            "suggested_next_steps": steps,
            "recovery_evidence": {
                "outcome_verification": observed_outcome,
                "handler_acknowledged": handler_acknowledged,
                "handler_acknowledgement_is_success_proof": False,
                "reconciliation_required": reconciliation_required,
            },
            "retry_safety": retry_safety,
            "automatic_action_taken": False,
            "automatic_retry_allowed": False,
            "requires_human_review": status in {
                Status.FLAGGED.value, Status.FAILED.value, Status.INEFFECTIVE.value,
                Status.BLOCKED.value, Status.CANCELLED.value, Status.SKIPPED.value,
            },
        }

    async def _execute_due_async(self) -> None:
        due = []
        successful = {Status.COMPLETED, Status.VERIFIED}
        terminal_unsuccessful = {
            Status.FAILED, Status.SKIPPED, Status.CANCELLED,
            Status.INEFFECTIVE, Status.BLOCKED,
        }
        for tid, task in list(self.tasks.items()):
            if task.status not in (Status.PLANNED, Status.APPROVED) or tid in self._executing:
                continue
            dependencies = list(getattr(task, "depends_on", []) or [])
            if not dependencies:
                due.append(tid)
                continue
            dependency_tasks = [self.tasks.get(dep_id) for dep_id in dependencies]
            missing = [dep_id for dep_id, dep_task in zip(dependencies, dependency_tasks) if dep_task is None]
            failed = [
                dep_id for dep_id, dep_task in zip(dependencies, dependency_tasks)
                if dep_task is not None and dep_task.status in terminal_unsuccessful
            ]
            if missing or failed:
                task.status = Status.BLOCKED
                task.error = "mission_dependency_unavailable:" + ",".join(missing + failed)
                task.result = {"status": "blocked", "missing_dependencies": missing, "unsuccessful_dependencies": failed}
                await self.store.save_task(task)
                _publish_event("task_blocked", {"task_id": tid, "dependencies": missing + failed})
                continue
            if all(dep_task.status in successful for dep_task in dependency_tasks):
                due.append(tid)
        if not due:
            return
        await asyncio.gather(
            *[self._execute_with_semaphore(tid) for tid in due],
            return_exceptions=True,
        )

    async def _execute_with_semaphore(self, task_id: str) -> None:
        async with self._execution_semaphore:
            await self._execute_async(task_id)

    async def _invoke_capability_handler(self, handler: Callable, params: dict) -> Any:
        if handler is None:
            return {"status": "error", "reason": "no handler bound"}
        params = params or {}
        if inspect.iscoroutinefunction(handler):
            return await handler(params)
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, handler, params)
        if inspect.isawaitable(result):
            return await result
        return result

    def set_authority_fence(self, fence: Any) -> None:
        self._authority_fence = fence
        log.info("Director legacy authority fence installed")

    async def execute_capability(self, cap_name: str, params: Optional[dict] = None) -> Dict[str, Any]:
        params = params or {}
        if self._authority_fence is not None and not self._authority_fence.allows_legacy_execution(cap_name):
            return self._authority_fence.fence_result(
                component="Director", capability=cap_name, params=params,
            )
        cap = self.capabilities.get(cap_name)
        if not cap:
            return {"ok": False, "error": f"unknown_capability:{cap_name}"}
        if not cap.enabled:
            return {"ok": False, "error": f"capability_disabled:{cap_name}"}
        if getattr(cap, "proposal_capable", False):
            return {
                "ok": False,
                "status": "governance_required",
                "error": "proposal_governed_handoff_required",
                "capability": cap_name,
            }
        if self.policy.requires_approval(cap):
            return {
                "ok": False,
                "status": "approval_required",
                "error": "director_policy_requires_approval",
                "capability": cap_name,
                "effective_authority": self.policy.authority(cap).value,
            }
        handler = getattr(cap, "handler", None)
        if handler is None:
            return {"ok": False, "error": f"no_handler:{cap_name}"}
        hname = getattr(handler, "__name__", "") or ""
        if hname.startswith("placeholder_") or hname == "<lambda>":
            return {
                "ok": False,
                "error": "handler_not_bound",
                "capability": cap_name,
                "status": "unavailable",
            }
        try:
            result = await self._invoke_capability_handler(handler, params)
            ok = self._handler_result_ok(result)
            out = {"ok": ok, "capability": cap_name, "result": result}
            if not ok and isinstance(result, dict):
                out["error"] = result.get("error") or result.get("status") or "capability_failed"
            return out
        except Exception as exc:
            log.exception("execute_capability %s failed", cap_name)
            return {"ok": False, "error": str(exc)[:300], "capability": cap_name}

    def _extract_target(self, task: Task) -> Optional[str]:
        cap = self.capabilities.get(task.capability)
        if cap is None or not getattr(cap, "target_param", None):
            return None
        value = task.params.get(cap.target_param)
        if value is None:
            return None
        return str(value)

    async def _execute_async(self, task_id: str) -> Task:
        task = self.tasks.get(task_id)
        if not task:
            raise KeyError(f"Task {task_id} not found")

        runnable_states = {Status.PLANNED, Status.APPROVED, Status.APPROVED_PENDING_EXECUTION}
        if task.status not in runnable_states:
            return task

        start = time.monotonic()

        if any(str(source).startswith("engine_signal:") for source in (task.sources or [])) and not task.proposal_id:
            task.status = Status.APPROVAL
            task.error = "engine_signal_requires_proposal_governance"
            task.result = {
                "status": "deferred",
                "reason": "engine_recommendations_require_proposal_backed_governed_handoff",
            }
            await self.store.save_task(task)
            _publish_event("engine_signal_execution_deferred", {
                "task_id": task.id,
                "capability": task.capability,
                "reason": task.error,
            })
            return task

        if task.proposal_id:
            cap = self.capabilities.get(task.capability)
            governed_handoff = getattr(cap, "governed_handoff", None) if cap else None
            proposal = self.proposals.get(task.proposal_id)
            if not callable(governed_handoff) or proposal is None:
                task.status = Status.APPROVAL
                task.error = "canonical_governed_handoff_required"
                task.result = {
                    "status": "deferred",
                    "reason": "canonical_governed_handoff_not_configured" if not callable(governed_handoff) else "proposal_not_found",
                }
                await self.store.save_task(task)
                _publish_event("proposal_execution_deferred", {
                    "proposal_id": task.proposal_id,
                    "task_id": task.id,
                    "reason": task.error,
                })
                return task

            async with self._lock:
                if task_id in self._executing:
                    return task
                self._executing.add(task_id)
            try:
                task.status = Status.EXECUTING
                task.executed_at = now()
                persisted = await self.store.save_task(task)
                if persisted is False:
                    task.status = Status.FLAGGED
                    task.error = "execution_not_started:in_flight_state_persistence_failed"
                    task.result = {"status": "deferred", "reason": "task_state_persistence_unavailable"}
                    return task
                result = governed_handoff(task=task, proposal=proposal, actor=task.approved_by or "")
                if inspect.isawaitable(result):
                    result = await result
                task.result = safe(result if result is not None else {"status": "unknown"})
                has_canonical_receipt = (
                    isinstance(result, dict)
                    and bool(result.get("execution_id"))
                    and bool(result.get("intent_id"))
                )
                if (
                    isinstance(result, dict)
                    and result.get("ok") is True
                    and result.get("status") == "succeeded"
                    and has_canonical_receipt
                ):
                    task.status = Status.COMPLETED
                    task.error = None
                    proposal.status = ProposalStatus.EXECUTED
                    proposal.outcome = "completed"
                    task.executed_at = now()
                else:
                    task.status = Status.APPROVAL
                    status = result.get("status") if isinstance(result, dict) else None
                    reason = status or "unknown"
                    if isinstance(result, dict) and result.get("ok") is True and not has_canonical_receipt:
                        reason = "missing_canonical_receipt_ids"
                    task.error = f"governed_handoff_not_confirmed:{reason}"
                await self.store.save_task(task)
                _publish_event("proposal_governed_handoff_result", {
                    "proposal_id": task.proposal_id,
                    "task_id": task.id,
                    "status": task.status.value,
                    "error": task.error,
                })
                return task
            except Exception as exc:
                task.status = Status.APPROVAL
                task.error = "governed_handoff_error"
                task.result = {"status": "unknown", "error": sanitize_error_message(exc)}
                await self.store.save_task(task)
                log.warning("Governed proposal handoff failed for task %s: %s", task.id, sanitize_error_message(exc))
                return task
            finally:
                async with self._lock:
                    self._executing.discard(task_id)

        async with self._lock:
            if task_id in self._executing:
                return task
            self._executing.add(task_id)

        try:
            if self._authority_fence is not None and not self._authority_fence.allows_legacy_execution(task.capability):
                task.status = Status.APPROVAL
                task.error = "legacy_runtime_fenced:canonical_governance_required"
                task.result = self._authority_fence.fence_result(
                    component="Director", capability=task.capability, params=task.params,
                )
                await self.store.save_task(task)
                _publish_event("legacy_action_fenced", task.result)
                return task

            cap = self.capabilities[task.capability]

            if not cap.enabled:
                task.status = Status.SKIPPED
                task.error = "Capability disabled"
                await self.store.save_task(task)
                return task

            if self.policy.requires_approval(cap) and task.status != Status.APPROVED:
                task.status = Status.APPROVAL
                await self.store.save_task(task)
                return task

            task.status = Status.EXECUTING
            task.executed_at = now()
            persisted = await self.store.save_task(task)
            if persisted is False:
                task.status = Status.FLAGGED
                task.error = "execution_not_started:in_flight_state_persistence_failed"
                task.result = {"status": "deferred", "reason": "task_state_persistence_unavailable"}
                return task

            needs_verification = bool(
                getattr(cap, "outcome_predicate", None)
                and getattr(cap, "verification_window_seconds", 0) > 0
            )
            pre_state = self._snapshot_observations() if needs_verification else {}

            result = await self._invoke_capability_handler(cap.handler, task.params)

            if not self._handler_result_ok(result):
                err = None
                if isinstance(result, dict):
                    err = result.get("error") or result.get("message") or result.get("status")
                task.status = Status.FAILED
                task.error = str(err or "capability_result_not_ok")[:500]
                task.result = safe(result or {"ok": False})
                self.history.append(task)
                await self.store.save_task(task)
                _publish_event("task_failed", {
                    "task_id": task.id,
                    "description": task.description,
                    "capability": task.capability,
                    "error": task.error,
                })
                self._record_decision(f"Failed: {task.description}", task.error, task, "failed")
                return task

            if needs_verification:
                await asyncio.sleep(cap.verification_window_seconds)
                post_state = self._snapshot_observations()
                verification_error = None
                try:
                    verified = cap.outcome_predicate(task.params, pre_state, post_state)
                except Exception as exc:
                    log.exception(
                        "Outcome predicate failed for %s: %s",
                        task.capability, sanitize_error_message(exc),
                    )
                    verified = None
                    verification_error = "outcome_predicate_error"

                if verified is True:
                    task.status = Status.VERIFIED
                    task.result = safe({
                        **(result if isinstance(result, dict) else {}),
                        "verified": True,
                        "outcome_verification": {"outcome": "verified"},
                    })
                elif verified is False:
                    task.status = Status.INEFFECTIVE
                    task.result = safe({
                        **(result if isinstance(result, dict) else {}),
                        "verified": False,
                        "outcome_verification": {"outcome": "ineffective", "reason": "authoritative_state_mismatch"},
                    })
                else:
                    task.status = Status.FLAGGED
                    task.error = verification_error or "outcome_verification_unknown"
                    task.result = safe({
                        **(result if isinstance(result, dict) else {}),
                        "verified": None,
                        "outcome_verification": {"outcome": "unknown", "reason": task.error},
                    })
            else:
                task.status = Status.COMPLETED
                task.result = safe(result or {})

            self.history.append(task)
            await self.store.save_task(task)

            _publish_event("task_completed", {
                "task_id": task.id,
                "description": task.description,
                "capability": task.capability,
                "status": task.status.value,
            })

            if task.status == Status.VERIFIED:
                _outcome = "verified"
            elif task.status == Status.INEFFECTIVE:
                _outcome = "ineffective"
            elif task.status == Status.FLAGGED and isinstance(task.result, dict) and (task.result.get("outcome_verification") or {}).get("outcome") == "unknown":
                _outcome = "unknown"
            else:
                _outcome = "completed"
            self._record_decision(f"Executed: {task.description}", task.reason, task, _outcome)

            if task.proposal_id and task.proposal_id in self.proposals and _outcome != "unknown":
                proposal = self.proposals[task.proposal_id]
                proposal.status = ProposalStatus.EXECUTED
                proposal.outcome = _outcome

            return task

        except Exception as exc:
            task.status = Status.FAILED
            task.error = sanitize_error_message(exc)
            task.result = {"error": sanitize_error_message(exc)}
            self.history.append(task)
            await self.store.save_task(task)
            _publish_event("task_failed", {
                "task_id": task.id,
                "description": task.description,
                "error": task.error,
            })
            self._record_decision(f"Failed: {task.description}", sanitize_error_message(exc), task, "failed")
            if task.proposal_id and task.proposal_id in self.proposals:
                proposal = self.proposals[task.proposal_id]
                proposal.status = ProposalStatus.EXECUTED
                proposal.outcome = "failed"
            return task

        finally:
            duration = (time.monotonic() - start) * 1000
            target = self._extract_target(task)
            try:
                signal = self.learning_loop.record_outcome(task, duration, target=target)
                if signal is not None:
                    self._learn(task, signal=signal)
            except Exception as exc:
                log.exception(
                    "Director: failed to record learning signal for %s: %s",
                    task.id, sanitize_error_message(exc),
                )
            async with self._lock:
                self._executing.discard(task_id)

    def _create_task(
        self,
        sector: Sector,
        desc: str,
        cap_name: str,
        reason: str,
        confidence: float = 0.8,
        params: dict = None,
        source: str = None,
        success_criteria: Optional[List[str]] = None,
        impact_score: float = 50.0,
        mission_priority: float = 50.0,
        evidence_observed_at: Optional[datetime] = None,
        condition_fingerprint: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> Task:
        cap = self.capabilities[cap_name]
        normalized_params = params or {}

        is_proposal_task = bool(source and str(source).startswith("proposal:"))
        active_statuses = {
            Status.PLANNED, Status.APPROVAL, Status.APPROVED,
            Status.APPROVED_PENDING_EXECUTION, Status.EXECUTING,
        }
        if not is_proposal_task:
            terminal_statuses = {
                Status.COMPLETED, Status.VERIFIED, Status.INEFFECTIVE,
                Status.FAILED, Status.CANCELLED, Status.SKIPPED, Status.BLOCKED, Status.FLAGGED,
            }
            for existing in self.tasks.values():
                same_intent = existing.capability == cap_name and existing.params == normalized_params
                if not same_intent:
                    continue
                if existing.status in active_statuses:
                    if source and source not in existing.sources:
                        existing.sources.append(source)
                    return existing
                if existing.status in terminal_statuses:
                    prior_fingerprint = getattr(existing, "condition_fingerprint", None)
                    if condition_fingerprint:
                        if prior_fingerprint == condition_fingerprint:
                            if source and source not in existing.sources:
                                existing.sources.append(source)
                            return existing
                        continue
                    prior_evidence = getattr(existing, "evidence_observed_at", None)
                    if evidence_observed_at is not None and prior_evidence == evidence_observed_at:
                        if source and source not in existing.sources:
                            existing.sources.append(source)
                        return existing
                    if evidence_observed_at is None and existing.created_at >= now() - timedelta(minutes=15):
                        if source and source not in existing.sources:
                            existing.sources.append(source)
                        return existing

        authority = self.policy.authority(cap)
        task = Task(
            id=f"dir_{uuid.uuid4().hex[:14]}",
            sector=sector,
            description=desc,
            capability=cap_name,
            authority=authority,
            risk=cap.risk,
            params=normalized_params,
            status=Status.APPROVAL if self.policy.requires_approval(cap) else Status.PLANNED,
            confidence=confidence,
            reason=reason,
            sources=[source] if source else [],
            success_criteria=list(success_criteria or []),
            impact_score=max(0.0, min(100.0, float(impact_score))),
            mission_priority=max(0.0, min(100.0, float(mission_priority))),
            evidence_observed_at=evidence_observed_at,
            condition_fingerprint=condition_fingerprint,
            workspace_id=str(workspace_id).strip() if workspace_id else None,
        )
        self.tasks[task.id] = task
        if len(self.tasks) > self.MAX_TASKS:
            removable = [
                tid for tid, t in self.tasks.items()
                if t.status in (
                    Status.COMPLETED, Status.VERIFIED, Status.INEFFECTIVE,
                    Status.FAILED, Status.CANCELLED,
                )
            ]
            for tid in removable[:500]:
                del self.tasks[tid]
        if len(self.tasks) > self.MAX_TASKS:
            oldest = sorted(self.tasks.items(), key=lambda x: x[1].created_at)[:500]
            for tid, _ in oldest:
                del self.tasks[tid]
        _publish_event("task_created", {
            "task_id": task.id,
            "description": desc,
            "capability": cap_name,
            "sector": sector.value,
        })
        self._record_decision(f"Planned: {desc}", reason, task, outcome="planned", stage="planning")
        return task

    @staticmethod
    def _condition_fingerprint(capability: str, params: dict, source: Optional[str] = None) -> str:
        return capability + "|" + json.dumps(params or {}, sort_keys=True, separators=(",", ":"), default=str) + "|" + str(source or "")

    def decide(self) -> List[Task]:
        decisions = []
        health = self.health()
        if health["score"] < 90:
            decisions.append(self._create_task(
                Sector.HEALTH,
                "Run complete platform health scan",
                "run_health_scan",
                f"Health score is {health['score']}",
                0.99,
            ))
        decision_now = datetime.now(timezone.utc)
        for obs in self.observations.values():
            stamp = getattr(obs, "timestamp", None)
            if not isinstance(stamp, datetime):
                continue
            stamp_utc = stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)
            age_seconds = (decision_now - stamp_utc).total_seconds()
            if age_seconds < -30 or age_seconds > 300:
                continue
            for issue in obs.issues:
                typ = str(issue.get("type", "")).lower()
                if typ in {"failed_service", "service_down", "worker_failed"}:
                    decisions.append(self._create_task(
                        Sector.HEALTH, "Recover failed service", "restart_failed_service",
                        "Recoverable service failure", 0.9, issue, obs.source,
                        condition_fingerprint=self._condition_fingerprint("restart_failed_service", issue, obs.source),
                    ))
                elif typ in {"security_issue", "vulnerability", "credential_exposure"}:
                    decisions.append(self._create_task(
                        Sector.SECURITY, "Prepare security remediation",
                        "prepare_security_remediation", "Security issue observed",
                        0.97, issue, obs.source, evidence_observed_at=obs.timestamp,
                        condition_fingerprint=self._condition_fingerprint("prepare_security_remediation", issue, obs.source),
                    ))
                elif typ in {"brand_inactive", "engagement_decline"}:
                    decisions.append(self._create_task(
                        Sector.BRANDS, "Queue brand revival", "queue_brand_revival",
                        "Brand performance declined", 0.87, issue, obs.source,
                        condition_fingerprint=self._condition_fingerprint("queue_brand_revival", issue, obs.source),
                    ))
                elif typ in {"content_gap", "schedule_gap"}:
                    decisions.append(self._create_task(
                        Sector.CONTENT, "Optimize content schedule",
                        "optimize_content_schedule", "Content gap observed",
                        0.88, issue, obs.source, evidence_observed_at=obs.timestamp,
                        condition_fingerprint=self._condition_fingerprint("optimize_content_schedule", issue, obs.source),
                    ))
        engine_signals = self._engine_decision_signals().get("accepted", [])
        for signal in engine_signals:
            capability = self.capabilities.get(signal["capability"])
            if capability is None or not capability.enabled:
                continue
            task = self._create_task(
                capability.sector,
                signal["description"],
                capability.name,
                f"Engine signal from {signal['observer']}: {signal['rationale']}",
                signal["confidence"],
                signal["params"],
                f"engine_signal:{signal['observer']}",
                success_criteria=signal["success_criteria"],
                impact_score=signal["impact_score"],
                mission_priority=signal["mission_priority"],
                evidence_observed_at=getattr(self.observations.get(signal["observer"]), "timestamp", None),
                condition_fingerprint=self._condition_fingerprint(capability.name, signal["params"], "engine_signal:" + str(signal["observer"])),
            )
            if capability.authority == Authority.AUTO and task.status not in (
                Status.COMPLETED, Status.VERIFIED, Status.FAILED, Status.CANCELLED,
            ):
                task.status = Status.APPROVAL
                task.reason += " (engine recommendation requires explicit approval)"
            decisions.append(task)

        # Goal definitions describe long-term priorities; they are not evidence.
        # Do not turn them into executable tasks without a fresh, scoped signal.
        # Real work enters through fresh observer issues and validated engine
        # signals above, each carrying its own target and evidence contract.

        seen = set()
        unique = []
        for task in decisions:
            key = (task.sector, task.capability, task.description)
            if key not in seen:
                seen.add(key)
                unique.append(task)
        for task in unique:
            target = self._extract_target(task)
            task.confidence = self.learning_loop.adjust_confidence(task, target=target)
            if self.learning_loop.should_escalate(task.sector, task.capability, target=target):
                task.status = Status.APPROVAL
                suffix = f":{target}" if target else ""
                task.reason += f" (low success rate for {task.sector.value}:{task.capability}{suffix})"
            if self.learning_loop.should_skip(task.sector, task.capability, target=target):
                task.status = Status.SKIPPED
                task.error = f"Capability {task.capability} unreliable"
                if target:
                    task.error += f" for target {target}"
                else:
                    task.error += f" for {task.sector.value}"
            if self.learning_loop.get_recurring_failures(task.capability, threshold=3, target=target):
                task.status = Status.APPROVAL
                suffix = f" for {target}" if target else ""
                task.reason += f" (recurring failures detected{suffix})"
        return sorted(unique, key=self._priority, reverse=True)

    def _priority(self, task: Task) -> float:
        impact = max(0.0, min(100.0, float(getattr(task, "impact_score", 50.0))))
        mission_priority = max(0.0, min(100.0, float(getattr(task, "mission_priority", 50.0))))
        confidence = max(0.0, min(1.0, float(task.confidence)))
        return (
            {Authority.ALERT: 1000, Authority.SUGGEST: 700, Authority.NOTIFY: 500, Authority.AUTO: 300}[task.authority]
            + {Risk.CRITICAL: 100, Risk.HIGH: 70, Risk.MEDIUM: 40, Risk.LOW: 10}[task.risk]
            + confidence * 100
            + impact * 0.5
            + mission_priority * 0.25
        )

    def approve(self, task_id: str, actor: str = "admin") -> Task:
        task = self.tasks.get(task_id)
        if task is None:
            raise KeyError(f"Task {task_id} not found")
        if task.status not in (Status.APPROVAL, Status.FLAGGED):
            raise ValueError("Task is not awaiting approval")
        task.status = Status.APPROVED
        task.approved_at = now()
        task.approved_by = actor
        return task

    def reject(self, task_id: str, actor: str = "admin", reason: str = "") -> Task:
        task = self.tasks.get(task_id)
        if task is None:
            raise KeyError(f"Task {task_id} not found")
        task.status = Status.CANCELLED
        task.error = f"Rejected by {actor}: {reason}"
        return task

    def organism_state(self, max_age_seconds: int = 300) -> dict:
        now_utc = datetime.now(timezone.utc)
        max_age = max(1, int(max_age_seconds))
        entries = []
        fresh_count = stale_count = unknown_count = invalid_count = 0
        observed_statuses = []

        for name, obs in sorted(self.observations.items()):
            stamp = getattr(obs, "timestamp", None)
            age_seconds = None
            freshness = "invalid_timestamp"
            if isinstance(stamp, datetime):
                try:
                    stamp_utc = stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)
                    age_seconds = (now_utc - stamp_utc).total_seconds()
                    if age_seconds < -30:
                        freshness = "invalid_timestamp"
                        invalid_count += 1
                    elif age_seconds > max_age:
                        freshness = "stale"
                        stale_count += 1
                    else:
                        freshness = "fresh"
                        fresh_count += 1
                except (OverflowError, ValueError):
                    invalid_count += 1
            else:
                invalid_count += 1

            source_status = getattr(obs, "status", Health.UNKNOWN)
            source_status = source_status.value if hasattr(source_status, "value") else str(source_status).lower()
            if source_status not in {item.value for item in Health}:
                source_status = Health.UNKNOWN.value
            if source_status == Health.UNKNOWN.value:
                unknown_count += 1
            elif freshness == "fresh":
                observed_statuses.append(source_status)

            entries.append({
                "observer": name,
                "source": str(getattr(obs, "source", name)),
                "sector": getattr(getattr(obs, "sector", None), "value", str(getattr(obs, "sector", "unknown"))),
                "reported_status": source_status,
                "freshness": freshness,
                "age_seconds": round(age_seconds, 2) if age_seconds is not None else None,
                "observed_at": iso(stamp) if isinstance(stamp, datetime) else None,
                "metrics": dict(getattr(obs, "metrics", {}) or {}),
                "issues": list(getattr(obs, "issues", []) or []),
                "message": str(getattr(obs, "message", "") or ""),
            })

        if not entries:
            condition = "unknown"
        elif fresh_count == 0 and stale_count + invalid_count > 0:
            condition = "stale"
        elif stale_count or invalid_count or unknown_count:
            condition = "degraded_evidence"
        else:
            severity = {
                Health.NOMINAL.value: 0, Health.DEGRADED.value: 1,
                Health.CRITICAL.value: 2, Health.OFFLINE.value: 3,
                Health.UNKNOWN.value: 4,
            }
            condition = max(observed_statuses, key=lambda status: severity.get(status, 4)) if observed_statuses else "unknown"

        return {
            "condition": condition,
            "evidence_policy": {
                "max_age_seconds": max_age,
                "missing_values_synthesized": False,
                "health_is_source_reported": True,
            },
            "summary": {
                "observation_count": len(entries),
                "fresh_count": fresh_count,
                "stale_count": stale_count,
                "invalid_timestamp_count": invalid_count,
                "unknown_status_count": unknown_count,
                "issue_count": sum(len(entry["issues"]) for entry in entries),
            },
            "last_observation": iso(self.last_observation),
            "observations": entries,
        }

    def _engine_decision_signals(self, max_age_seconds: int = 300) -> dict:
        now_utc = datetime.now(timezone.utc)
        max_age = max(1, int(max_age_seconds))
        accepted, rejected = [], []
        candidate_signals = defaultdict(lambda: {"observers": set(), "sectors": set(), "descriptions": set(), "observations": 0})
        coverage = []
        fresh_statuses_by_sector: Dict[str, set] = defaultdict(set)
        fresh_observations = {}
        for candidate_name, candidate in self.observations.items():
            candidate_stamp = getattr(candidate, "timestamp", None)
            if not isinstance(candidate_stamp, datetime):
                continue
            candidate_stamp_utc = candidate_stamp.replace(tzinfo=timezone.utc) if candidate_stamp.tzinfo is None else candidate_stamp.astimezone(timezone.utc)
            candidate_age = (now_utc - candidate_stamp_utc).total_seconds()
            if -30 <= candidate_age <= max_age:
                candidate_status = getattr(getattr(candidate, "status", None), "value", getattr(candidate, "status", None))
                candidate_status = str(candidate_status).strip().lower()
                candidate_sector = getattr(getattr(candidate, "sector", None), "value", str(getattr(candidate, "sector", "unknown")))
                fresh_statuses_by_sector[candidate_sector].add(candidate_status)
                fresh_observations[candidate_name] = (candidate_status, candidate_sector)
        conflicted_sectors = {sector for sector, statuses in fresh_statuses_by_sector.items() if len(statuses) > 1}
        healthy_statuses = {"healthy", "nominal", "ok", "ready"}

        for observer_name, obs in sorted(self.observations.items()):
            metrics_for_coverage = getattr(obs, "metrics", {}) or {}
            raw_for_coverage = metrics_for_coverage.get("director_signals") if isinstance(metrics_for_coverage, dict) else None
            declared_contract = metrics_for_coverage.get("director_signal_contract") if isinstance(metrics_for_coverage, dict) else None
            coverage_entry = {
                "observer": observer_name,
                "sector": getattr(getattr(obs, "sector", None), "value", str(getattr(obs, "sector", "unknown"))),
                "has_signal_contract": isinstance(raw_for_coverage, list) and declared_contract == "metrics.director_signals/v1",
                "declared_contract": declared_contract,
                "signal_count": len(raw_for_coverage) if isinstance(raw_for_coverage, list) else 0,
                "status": str(getattr(getattr(obs, "status", None), "value", getattr(obs, "status", "unknown"))).lower(),
            }
            coverage.append(coverage_entry)
            stamp = getattr(obs, "timestamp", None)
            if not isinstance(stamp, datetime):
                rejected.append({"observer": observer_name, "reason": "invalid_observation_timestamp"})
                continue
            stamp_utc = stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)
            age = (now_utc - stamp_utc).total_seconds()
            if age < -30 or age > max_age:
                rejected.append({"observer": observer_name, "reason": "observation_not_fresh"})
                continue
            source_status = getattr(getattr(obs, "status", None), "value", getattr(obs, "status", None))
            source_status = str(source_status).strip().lower()
            source_sector = getattr(getattr(obs, "sector", None), "value", str(getattr(obs, "sector", "unknown")))
            if source_status not in healthy_statuses:
                metrics = getattr(obs, "metrics", {}) or {}
                raw_signals = metrics.get("director_signals", []) if isinstance(metrics, dict) else []
                if isinstance(raw_signals, list):
                    for index, _signal in enumerate(raw_signals[:50]):
                        rejected.append({"observer": observer_name, "index": index, "reason": "observer_status_not_healthy", "reported_status": source_status})
                continue
            if source_sector in conflicted_sectors:
                metrics = getattr(obs, "metrics", {}) or {}
                raw_signals = metrics.get("director_signals", []) if isinstance(metrics, dict) else []
                if isinstance(raw_signals, list):
                    for index, _signal in enumerate(raw_signals[:50]):
                        rejected.append({"observer": observer_name, "index": index, "reason": "sector_status_conflict", "sector": source_sector})
                continue
            metrics = getattr(obs, "metrics", {}) or {}
            raw_signals = metrics.get("director_signals", []) if isinstance(metrics, dict) else []
            if not isinstance(raw_signals, list):
                rejected.append({"observer": observer_name, "reason": "signals_must_be_a_list"})
                continue
            declared_contract = metrics.get("director_signal_contract") if isinstance(metrics, dict) else None
            if declared_contract is not None and declared_contract != "metrics.director_signals/v1":
                rejected.append({"observer": observer_name, "reason": "unsupported_signal_contract", "declared_contract": str(declared_contract)[:100]})
                continue
            if len(raw_signals) > 50:
                rejected.append({"observer": observer_name, "reason": "signal_count_limit_exceeded", "signal_count": len(raw_signals), "limit": 50})
                continue
            for index, signal in enumerate(raw_signals):
                reason = None
                if not isinstance(signal, dict):
                    reason = "signal_must_be_an_object"
                else:
                    cap_name = str(signal.get("capability", "")).strip()
                    capability = self.capabilities.get(cap_name)
                    if not cap_name or capability is None:
                        reason = "unknown_capability"
                        # Preserve only a bounded, sanitized advisory candidate.
                        # Unknown names never enter the executable capability registry.
                        if (
                            cap_name and len(cap_name) <= 80
                            and all(char.isalnum() or char in "._-" for char in cap_name)
                            and declared_contract == "metrics.director_signals/v1"
                        ):
                            candidate = candidate_signals[cap_name]
                            candidate["observers"].add(observer_name)
                            candidate["sectors"].add(source_sector)
                            candidate["observations"] += 1
                            description = signal.get("description", "") if isinstance(signal, dict) else ""
                            if isinstance(description, str) and description.strip():
                                candidate["descriptions"].add(description.strip()[:180])
                    elif not capability.enabled:
                        reason = "capability_disabled"
                    elif capability.sector != getattr(obs, "sector", None):
                        reason = "sector_mismatch"
                    elif not isinstance(signal.get("params", {}), dict):
                        reason = "params_must_be_an_object"
                    elif not str(signal.get("description", "")).strip():
                        reason = "description_required"
                    elif isinstance(signal.get("confidence", 0.5), bool) or not isinstance(signal.get("confidence", 0.5), (int, float)) or not 0 <= float(signal.get("confidence", 0.5)) <= 1:
                        reason = "confidence_out_of_range"
                if reason:
                    rejected.append({"observer": observer_name, "index": index, "reason": reason})
                    continue
                capability = self.capabilities[str(signal["capability"]).strip()]
                params = signal.get("params", {})
                if len(params) > 30:
                    rejected.append({"observer": observer_name, "index": index, "reason": "too_many_params"})
                    continue
                impact_score = signal.get("impact_score", 50) if isinstance(signal, dict) else 50
                mission_priority = signal.get("mission_priority", 50) if isinstance(signal, dict) else 50
                success_criteria = signal.get("success_criteria", []) if isinstance(signal, dict) else []
                if (isinstance(impact_score, bool) or not isinstance(impact_score, (int, float)) or not 0 <= float(impact_score) <= 100):
                    rejected.append({"observer": observer_name, "index": index, "reason": "impact_score_out_of_range"})
                    continue
                if (isinstance(mission_priority, bool) or not isinstance(mission_priority, (int, float)) or not 0 <= float(mission_priority) <= 100):
                    rejected.append({"observer": observer_name, "index": index, "reason": "mission_priority_out_of_range"})
                    continue
                if not isinstance(success_criteria, list) or len(success_criteria) > 10:
                    rejected.append({"observer": observer_name, "index": index, "reason": "invalid_success_criteria"})
                    continue
                criteria_valid = True
                for criterion in success_criteria:
                    if isinstance(criterion, str):
                        criteria_valid = bool(criterion.strip()) and len(criterion) <= 240
                    elif isinstance(criterion, dict):
                        criteria_valid = (
                            isinstance(criterion.get("observer"), str) and bool(criterion["observer"].strip())
                            and isinstance(criterion.get("metric_path"), str) and bool(criterion["metric_path"].strip())
                            and criterion.get("operator", "equals") in {"equals", "not_equals", "gt", "gte", "lt", "lte", "contains", "exists"}
                            and (criterion.get("operator", "equals") == "exists" or "expected" in criterion)
                            and len(criterion) <= 5
                        )
                    else:
                        criteria_valid = False
                    if not criteria_valid:
                        break
                if not criteria_valid:
                    rejected.append({"observer": observer_name, "index": index, "reason": "invalid_success_criteria"})
                    continue
                accepted.append({
                    "observer": observer_name,
                    "sector": getattr(obs.sector, "value", str(obs.sector)),
                    "capability": capability.name,
                    "description": str(signal["description"]).strip()[:240],
                    "params": dict(params),
                    "confidence": float(signal.get("confidence", 0.5)),
                    "impact_score": float(impact_score),
                    "mission_priority": float(mission_priority),
                    "success_criteria": [item.strip() if isinstance(item, str) else dict(item) for item in success_criteria],
                    "rationale": str(signal.get("rationale", "Engine recommendation; not independently verified."))[:500],
                    "observed_at": iso(stamp),
                })
        advisory_candidates = [
            {
                "name": name,
                "observers": sorted(item["observers"]),
                "sectors": sorted(item["sectors"]),
                "descriptions": sorted(item["descriptions"])[:3],
                "observation_count": item["observations"],
                "catalogue_status": "not_registered",
                "execution_status": "not_executable",
                "review_required": True,
            }
            for name, item in sorted(candidate_signals.items())
        ]
        return {
            "accepted": accepted,
            "rejected": rejected,
            "capability_candidates": advisory_candidates,
            "capability_candidate_policy": {
                "source": "fresh_healthy_declared_engine_signal_contract",
                "auto_registered": False,
                "auto_enabled": False,
                "executable": False,
                "human_review_required": True,
            },
            "coverage": coverage,
            "coverage_summary": {
                "observer_count": len(coverage),
                "contract_enabled_observers": sum(1 for item in coverage if item["has_signal_contract"]),
                "observers_without_signal_contract": [item["observer"] for item in coverage if not item["has_signal_contract"]],
            },
            "contract": "metrics.director_signals/v1",
        }

    def decision_context(self, max_age_seconds: int = 300) -> dict:
        state = self.organism_state(max_age_seconds=max_age_seconds)
        facts = []
        inferences = []
        unknowns = []
        by_sector: Dict[str, List[dict]] = defaultdict(list)

        for item in state["observations"]:
            sector = item["sector"]
            by_sector[sector].append(item)
            evidence_ref = item["observer"]
            if item["freshness"] != "fresh":
                unknowns.append({
                    "kind": "observation_not_current",
                    "observer": evidence_ref,
                    "freshness": item["freshness"],
                    "reported_status": item["reported_status"],
                    "age_seconds": item["age_seconds"],
                })
                continue
            if item["reported_status"] == Health.UNKNOWN.value:
                unknowns.append({
                    "kind": "source_status_unknown",
                    "observer": evidence_ref,
                    "sector": sector,
                })
                continue
            facts.append({
                "kind": "source_reported_status",
                "service_id": evidence_ref,
                "observer": evidence_ref,
                "source": item["source"],
                "sector": sector,
                "status": item["reported_status"],
                "observed_at": item["observed_at"],
            })
            for issue in item["issues"]:
                inferences.append({
                    "kind": "reported_issue_requires_triage",
                    "observer": evidence_ref,
                    "issue_type": str(issue.get("type", "unspecified")),
                    "interpretation": "The observer reported an issue; impact and remediation are not independently verified.",
                })

        conflicts = []
        for sector, items in sorted(by_sector.items()):
            current = [item for item in items if item["freshness"] == "fresh" and item["reported_status"] != Health.UNKNOWN.value]
            statuses = sorted({item["reported_status"] for item in current})
            if len(statuses) > 1:
                conflicts.append({
                    "kind": "sector_status_disagreement",
                    "sector": sector,
                    "statuses": statuses,
                    "observers": [item["observer"] for item in current],
                    "interpretation": "Fresh observers disagree; do not collapse this into a single authoritative status.",
                })

        if not state["observations"]:
            unknowns.append({"kind": "no_observations", "interpretation": "No engine health facts are currently available."})

        raw_engine_signals = self._engine_decision_signals(max_age_seconds=max_age_seconds)
        # Decision context is a planner/read-model contract, not an execution
        # payload. Keep signal intent and evidence, but never expose raw params.
        engine_signals = {
            **raw_engine_signals,
            "accepted": [
                {key: value for key, value in signal.items() if key != "params"}
                for signal in raw_engine_signals.get("accepted", [])
            ],
        }
        return {
            "version": 1,
            "generated_at": iso(datetime.now(timezone.utc)),
            "organism_condition": state["condition"],
            "engine_signal_contract": engine_signals["contract"],
            "engine_signals": engine_signals,
            "evidence_policy": {
                "max_age_seconds": max(1, int(max_age_seconds)),
                "facts_are_source_reported": True,
                "inferences_are_not_verified_outcomes": True,
                "missing_data_is_unknown": True,
                "engine_signals_require_fresh_healthy_observer": True,
                "fresh_sector_status_conflict_suppresses_engine_signals": True,
                "stale_issues_do_not_create_new_remediation_tasks": True,
                "engine_signal_parameters_excluded_from_context": True,
            },
            "facts": facts,
            "inferences": inferences,
            "unknowns": unknowns,
            "conflicts": conflicts,
            "observer_discovery": self._observer_discovery_evidence(),
        }

    def _observer_discovery_evidence(self) -> dict:
        """Expose why registered observers did or did not become live observers."""
        report = self.engine_discovery_report if isinstance(self.engine_discovery_report, dict) else {}
        categories = (
            "discovered", "missing_exports", "import_failures",
            "factory_failures", "unsupported_exports",
        )
        entries = {}
        for category in categories:
            values = report.get(category, [])
            entries[category] = [
                {
                    key: str(value.get(key, ""))[:240]
                    for key in ("observer", "module", "export", "kind", "reason", "sector")
                    if value.get(key) is not None
                }
                for value in values if isinstance(value, dict)
            ][:200]
        return {
            "status": "available" if report.get("registry_entries") else "unavailable",
            "registry_entries": int(report.get("registry_entries", len(ENGINE_REGISTRY)) or 0),
            "discovered_count": int(report.get("discovered_count", 0) or 0),
            "missing_export_count": int(report.get("missing_export_count", 0) or 0),
            "import_failure_count": int(report.get("import_failure_count", 0) or 0),
            "factory_failure_count": int(report.get("factory_failure_count", 0) or 0),
            "unsupported_export_count": int(report.get("unsupported_export_count", 0) or 0),
            "entries": entries,
            "read_only": True,
            "grants_execution_authority": False,
        }

    def strategic_context(self, max_age_seconds: int = 300, max_tasks: int = 100) -> dict:
        """Build a bounded, read-only platform context for strategic planning.

        This is planner input only. It contains no handler references, task
        parameters, execution results, credentials, or authority grants.
        """
        if isinstance(max_age_seconds, bool) or not isinstance(max_age_seconds, int) or not 1 <= max_age_seconds <= 86400:
            raise ValueError("max_age_seconds must be an integer between 1 and 86400")
        if isinstance(max_tasks, bool) or not isinstance(max_tasks, int) or not 1 <= max_tasks <= 500:
            raise ValueError("max_tasks must be an integer between 1 and 500")

        evidence = self.decision_context(max_age_seconds=max_age_seconds)
        health = self.health()
        sectors = {
            name: {
                "status": value.get("status", "unknown"),
                "score": value.get("score"),
                "observer_count": value.get("observers", 0),
                "non_nominal_observer_count": value.get("non_nominal_observers", 0),
                "unknown_observer_count": value.get("unknown_observers", 0),
                "reported_issue_record_count": value.get("reported_issue_records", 0),
            }
            for name, value in (health.get("sectors") or {}).items()
        }

        objectives = [
            {
                "objective_id": goal.id,
                "title": goal.name,
                "description": goal.description,
                "sector": goal.sector.value,
                "priority": int(goal.priority),
                "enabled": bool(goal.enabled),
                "model": "legacy_sector_goal",
                "success_criteria_defined": False,
            }
            for goal in sorted(self.goals.values(), key=lambda item: (-item.priority, item.id))
        ]

        capabilities = [
            {
                "name": capability.name,
                "sector": capability.sector.value,
                "description": capability.description,
                "authority": capability.authority.value,
                "risk": capability.risk.value,
                "enabled": bool(capability.enabled),
                "proposal_capable": bool(capability.proposal_capable),
                "handler_bound": self._handler_is_bound(capability.handler),
                "governed_handoff_bound": callable(capability.governed_handoff),
            }
            for capability in sorted(self.capabilities.values(), key=lambda item: item.name)
        ]

        tasks = sorted(
            self.tasks.values(),
            key=lambda item: item.created_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        status_counts = Counter(
            getattr(item.status, "value", str(item.status)) for item in self.tasks.values()
        )
        sector_counts = Counter(
            getattr(item.sector, "value", str(item.sector)) for item in self.tasks.values()
        )
        mission_ids = {item.mission_id for item in self.tasks.values() if item.mission_id}
        dependency_edges = sum(len(getattr(item, "depends_on", []) or []) for item in self.tasks.values())
        unresolved_dependencies = sum(
            1
            for item in self.tasks.values()
            for dependency_id in (getattr(item, "depends_on", []) or [])
            if dependency_id not in self.tasks
        )
        terminal = {Status.COMPLETED, Status.VERIFIED, Status.FAILED, Status.SKIPPED, Status.CANCELLED, Status.INEFFECTIVE, Status.BLOCKED}
        recent_tasks = [
            {
                "task_id": item.id,
                "mission_id": item.mission_id,
                "sector": item.sector.value,
                "capability": item.capability,
                "description": str(item.description)[:240],
                "status": getattr(item.status, "value", str(item.status)),
                "authority": item.authority.value,
                "risk": item.risk.value,
                "confidence": float(item.confidence),
                "sources": list(item.sources or []),
                "depends_on": list(getattr(item, "depends_on", []) or []),
                "success_criteria": list(getattr(item, "success_criteria", []) or []),
                "impact_score": float(getattr(item, "impact_score", 50.0)),
                "mission_priority": float(getattr(item, "mission_priority", 50.0)),
                "created_at": iso(item.created_at),
                "executed_at": iso(item.executed_at),
            }
            for item in tasks[:max_tasks]
        ]

        decision_stages = Counter(getattr(item, "stage", "execution") for item in self.decisions)
        decision_evidence_states = Counter(getattr(item, "evidence_state", "unknown") for item in self.decisions)
        uncalibrated_confidence_count = sum(
            1
            for item in self.decisions
            if getattr(item, "confidence_basis", {}).get("calibration") == "not_evidence_calibrated"
        )

        discovery = self.engine_discovery_report or {}
        binding_report = self.capability_binding_report or {}
        bound_capabilities = sorted(
            name for name, status in binding_report.items()
            if str(status).lower() in {"bound", "ready", "ok"}
        )

        return {
            "schema_version": "kdo.director.strategic-context.v1",
            "generated_at": iso(datetime.now(timezone.utc)),
            "scope": {"type": "platform", "workspace_id": None},
            "purpose": "bounded_strategic_planning_input",
            "authority": "none",
            "health_summary": {
                "status": health.get("status", "unknown"),
                "score": health.get("score"),
                "observer_count": health.get("observer_count", 0),
                "status_counts": dict(health.get("status_counts", {})),
                "non_nominal_observer_count": health.get("non_nominal_observer_count", 0),
                "unknown_observer_count": health.get("unknown_observer_count", 0),
                "reported_issue_record_count": health.get("issue_count", 0),
                "sectors": sectors,
            },
            "evidence": evidence,
            "platform_state": dict(self._platform_state_context),
            "objectives": {
                "model": "legacy_sector_goal",
                "workspace_scoped": False,
                "success_criteria_defined": False,
                "items": objectives,
            },
            "capabilities": {
                "count": len(capabilities),
                "items": capabilities,
                "publishing_owner": "brand_scheduler",
                "director_can_publish": False,
            },
            "missions": {
                "storage_scope": "current_memory_with_startup_restore",
                "task_count": len(self.tasks),
                "recent_task_limit": max_tasks,
                "recent_tasks": recent_tasks,
                "task_status_counts": dict(status_counts),
                "task_sector_counts": dict(sector_counts),
                "mission_count": len(mission_ids),
                "dependency_edge_count": dependency_edges,
                "unresolved_dependency_count": unresolved_dependencies,
                "active_task_count": sum(
                    1 for item in self.tasks.values() if item.status not in terminal
                ),
            },
            "learning": self.learning_loop.get_learning_report(),
            "decision_audit": {
                "record_count": len(self.decisions),
                "stage_counts": dict(decision_stages),
                "evidence_state_counts": dict(decision_evidence_states),
                "uncalibrated_confidence_count": uncalibrated_confidence_count,
            },
            "runtime_readiness": {
                "engine_registry_count": discovery.get("registry_entries", 0),
                "discovered_engine_count": discovery.get("discovered_count", 0),
                "missing_engine_export_count": discovery.get("missing_export_count", 0),
                "unsupported_engine_export_count": discovery.get("unsupported_export_count", 0),
                "capability_count": len(capabilities),
                "bound_capability_names": bound_capabilities,
                "binding_report_available": bool(binding_report),
            },
            "guardrails": {
                "context_grants_execution_authority": False,
                "sensitive_actions_require_existing_governance": True,
                "workspace_actions_require_workspace_scoped_authorization": True,
                "brand_publishing_owned_by_scheduler": True,
            },
            "limitations": [
                "Current Director objectives are legacy sector goals without machine-verifiable success criteria.",
                "Health scores are descriptive aggregates, not causal diagnoses.",
                "Unknown evidence must not be interpreted as healthy or failed.",
                "Learning rates are based only on verified outcomes; null means insufficient verified evidence.",
                "This context does not authorize execution or replace Council, DecisionGate, or Action Kernel admission.",
            ],
        }

    def health(self) -> dict:
        """Aggregate only fresh, explicit health evidence; unknown is not a score."""
        weights = {
            Health.NOMINAL: 100, Health.DEGRADED: 70, Health.CRITICAL: 25,
            Health.OFFLINE: 0,
        }
        max_age_seconds = 300
        current = now()
        groups: Dict[str, List[Observation]] = {}
        for obs in self.observations.values():
            groups.setdefault(obs.sector.value, []).append(obs)
        sectors = {}
        scores = []
        stale_count = 0
        for sector_name, observations in groups.items():
            known = []
            unknown_count = 0
            for observation in observations:
                status = observation.status
                if status == Health.UNKNOWN:
                    unknown_count += 1
                    continue
                stamp = getattr(observation, "timestamp", None)
                if not isinstance(stamp, datetime) or not getattr(observation, "timestamp_valid", True):
                    stale_count += 1
                    unknown_count += 1
                    continue
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                age = (current - stamp.astimezone(timezone.utc)).total_seconds()
                if age < -5 or age > max_age_seconds:
                    stale_count += 1
                    unknown_count += 1
                    continue
                known.append(observation)
            score = (
                sum(weights[item.status] for item in known) / len(known)
                if known else None
            )
            if score is not None:
                scores.append(score)
            sectors[sector_name] = {
                "status": self._status_label(score).value if score is not None else "unknown",
                "score": round(score, 1) if score is not None else None,
                "issues": sum(len(item.issues) for item in observations),
                "reported_issue_records": sum(len(item.issues) for item in observations),
                "non_nominal_observers": sum(1 for item in known if item.status != Health.NOMINAL),
                "unknown_observers": unknown_count,
                "known_observers": len(known),
                "observers": len(observations),
                "evidence_state": "available" if known else "unknown",
            }
        overall = sum(scores) / len(scores) if scores else None
        status_counts = Counter(
            str(getattr(item.status, "value", item.status)).lower()
            for item in self.observations.values()
        )
        issue_count = sum(len(item.issues) for item in self.observations.values())
        non_nominal_count = sum(
            count for status, count in status_counts.items()
            if status not in {"nominal", "healthy", "ok", "ready"}
        )
        known_count = sum(item["known_observers"] for item in sectors.values())
        unknown_evidence_count = sum(item["unknown_observers"] for item in sectors.values())
        evidence_state = (
            "unknown" if not known_count
            else "partial" if unknown_evidence_count
            else "available"
        )
        return {
            "contract_version": "kdo.health.aggregate.v1",
            "status": self._status_label(overall).value if overall is not None else "unknown",
            "score": round(overall, 1) if overall is not None else None,
            "evidence_state": evidence_state,
            "freshness_window_seconds": max_age_seconds,
            "known_observer_count": known_count,
            "observer_count": len(self.observers),
            "observed_record_count": len(self.observations),
            "stale_observer_count": stale_count,
            "unknown_observer_count": unknown_evidence_count,
            "sectors": sectors,
            "status_counts": dict(status_counts),
            "non_nominal_observer_count": non_nominal_count,
            "issue_count": issue_count,
            "timestamp": iso(current),
        }

    def _status_label(self, score: float) -> Health:
        if score >= 90:
            return Health.NOMINAL
        if score >= 60:
            return Health.DEGRADED
        if score > 0:
            return Health.CRITICAL
        return Health.OFFLINE

    def status(self) -> dict:
        health = self.health()
        counts = Counter(t.status.value for t in self.tasks.values())
        mode = "running" if self.running else "stopped"
        status_label = str(health.get("status") or mode)
        subsystems = []
        try:
            for node_id, node in (getattr(self.topology, "nodes", {}) or {}).items():
                st = getattr(node, "status", None) or getattr(node, "health", None) or "unknown"
                if hasattr(st, "value"):
                    st = st.value
                subsystems.append({
                    "id": str(node_id),
                    "name": str(getattr(node, "name", None) or node_id),
                    "code": str(getattr(node, "kind", None) or getattr(node, "type", None) or "node"),
                    "status": str(st).lower(),
                    "health": getattr(node, "score", None),
                    "latencyMs": getattr(node, "latency_ms", None),
                    "loadPercentage": getattr(node, "load", None),
                })
        except Exception:
            subsystems = []
        if not subsystems and isinstance(health.get("sectors"), dict):
            for sn, meta in health["sectors"].items():
                subsystems.append({
                    "id": str(sn), "name": str(sn), "code": "sector",
                    "status": str((meta or {}).get("status", "unknown")).lower(),
                    "health": (meta or {}).get("score"),
                })
        return {
            "name": "Kanyoza Director",
            "codename": "The Autonomous CEO",
            "version": self.VERSION,
            "running": self.running,
            "mode": mode,
            "status": status_label,
            "state": mode,
            "subsystems": subsystems,
            "nodes": subsystems,
            "uptime_hours": round((now() - self.started).total_seconds() / 3600, 2),
            "health": health,
            "observers": len(self.observers),
            "capabilities": len(self.capabilities),
            "sectors": len(Sector),
            "tasks": dict(counts),
            "pending_approvals": sum(1 for t in self.tasks.values() if t.status == Status.APPROVAL),
            "pending_proposals": sum(1 for p in self.proposals.values() if p.status == ProposalStatus.PENDING),
            "total_proposals": len(self.proposals),
            "total_signals": len(self.signals),
            "last_observation": iso(self.last_observation),
            "last_plan": iso(self.last_plan),
            "last_detector_run": iso(self._last_detector_run),
            "event_queue_depth": len(self._event_queue),
            "executing_tasks": len(self._executing),
            "diagnostics": {
                "engine_discovery": self.engine_discovery_report,
                "topology_nodes": len(self.topology.nodes),
                "current_root_cause": (
                    {
                        "service": self.current_root_cause.root_cause,
                        "confidence": self.current_root_cause.confidence,
                        "affected_count": len(self.current_root_cause.affected_services),
                    }
                    if self.current_root_cause else None
                ),
                "current_impact": self.current_impact,
                "cascading_predictions": self.current_predictions[:5],
                "recovery_plans": len(self.recovery_coordinator.recovery_plans),
            },
            "intelligence": {
                "learning_signals": len(self.learning_loop.signals),
                "adaptive_thresholds": dict(self.adaptive_thresholds.thresholds),
                "learning_report": self.learning_loop.get_learning_report(),
            },
        }

    def generate_strategic_outlook(self, horizon_days: int = 7) -> Optional[StrategicOutlook]:
        task_history = list(self.history)
        if len(task_history) < 10:
            return None
        daily_tasks = defaultdict(int)
        for t in task_history:
            day = t.executed_at.strftime("%Y-%m-%d") if t.executed_at else None
            if day:
                daily_tasks[day] += 1
        recent_daily_avg = mean(list(daily_tasks.values())[-7:]) if len(daily_tasks) >= 7 else 1
        predicted_volume = recent_daily_avg * horizon_days
        health_scores = [
            b.get("health", {}).get("score", 50)
            for b in list(self.briefings)[-20:]
            if isinstance(b, dict)
        ]
        if len(health_scores) >= 5:
            slope = (health_scores[-1] - health_scores[0]) / len(health_scores)
            predicted_health = clamp(health_scores[-1] + slope * horizon_days, 0, 100)
        else:
            predicted_health = self.health()["score"]
        risks = []
        if predicted_health < 60:
            risks.append("Health trajectory declining — intervention needed within 48h")
        failure_patterns = Counter(
            t.capability for t in task_history if t.status == Status.FAILED
        )
        for cap, count in failure_patterns.most_common(3):
            if count >= 2:
                risks.append(f"Recurring failure in {cap}: {count} failures")
        opportunities = []
        sectors_with_issues = Counter(
            obs.sector.value for obs in self.observations.values() if obs.issues
        )
        for sector, count in sectors_with_issues.most_common(2):
            opportunities.append(f"Address {count} issues in {sector}")
        if predicted_health > 85:
            opportunities.append("System healthy — ideal time for proactive optimization")
        return StrategicOutlook(
            horizon_days=horizon_days,
            predicted_health_score=round(predicted_health, 1),
            predicted_task_volume=int(predicted_volume),
            emerging_risks=risks,
            growth_opportunities=opportunities,
            recommended_focus_areas=self._derive_focus_areas(risks, opportunities),
            confidence=min(0.9, len(task_history) / 50),
        )

    def _derive_focus_areas(self, risks: List[str], opportunities: List[str]) -> List[str]:
        areas = []
        if any("health" in r.lower() for r in risks):
            areas.append("Stabilize platform health")
        if any("failure" in r.lower() for r in risks):
            areas.append("Address recurring failures")
        if any("improvement" in o.lower() for o in opportunities):
            areas.append("Execute optimization initiatives")
        if any("healthy" in o.lower() for o in opportunities):
            areas.append("Proactive growth and scaling")
        return areas or ["Monitor and maintain current state"]

    def allocate_attention(self) -> List[ResourceAllocation]:
        health = self.health()
        allocations = []
        total_priority = sum(g.priority for g in self.goals.values() if g.enabled)
        for sector_name, sector_health in health.get("sectors", {}).items():
            try:
                sector = Sector(sector_name)
            except ValueError:
                continue
            sector_goals = [g for g in self.goals.values() if g.sector == sector and g.enabled]
            importance = sum(g.priority for g in sector_goals) / max(total_priority, 1)
            sector_score = sector_health.get("score", 50)
            attention_needed = (100 - sector_score) / 100 * importance
            allocations.append(ResourceAllocation(
                sector=sector,
                current_health=sector_score,
                importance_weight=round(importance, 2),
                recommended_attention_pct=round(attention_needed * 100, 1),
                reasoning=(
                    f"Critical: low health ({sector_score}) + high importance ({importance:.0%})"
                    if sector_score < 60 and importance > 0.2
                    else "Monitor: stable but important"
                    if sector_score >= 60 and importance > 0.2
                    else "Maintain: healthy and lower priority"
                ),
            ))
        return sorted(allocations, key=lambda a: a.recommended_attention_pct, reverse=True)

    def generate_executive_summary(self) -> str:
        health = self.health()
        outlook = self.generate_strategic_outlook()
        allocations = self.allocate_attention()
        if health["score"] >= 90 and not any(
            s.get("status") in ("critical", "degraded", "unknown")
            for s in health.get("sectors", {}).values()
        ):
            opening = "The platform is operating excellently."
        elif health["score"] >= 90:
            opening = "The overall score is high, but sector-level degradation or unknown health remains."
        elif health["score"] >= 70:
            opening = "The platform is stable with minor areas for attention."
        elif health["score"] >= 50:
            opening = "The platform requires attention — several areas need intervention."
        else:
            opening = "URGENT: The platform is in a degraded state requiring immediate action."
        parts = [
            "## Kanyoza Platform Executive Summary",
            f"*{utcnow().strftime('%B %d, %Y — %H:%M UTC')}*", "",
            opening, "",
            f"### Health: {health['score']:.0f}/100 ({health['status'].upper()})",
            (
                f"Evidence: {health.get('observer_count', 0)} observers; "
                f"{health.get('non_nominal_observer_count', 0)} non-nominal; "
                f"{health.get('issue_count', 0)} issue records reported."
            ),
        ]
        if health.get("issue_count", 0) == 0 and health.get("non_nominal_observer_count", 0) > 0:
            parts.append(
                "Degraded or unknown health statuses have no attached issue records; "
                "treat this as an evidence gap, not proof that there are zero issues."
            )
        pending = [p for p in self.proposals.values() if p.status == ProposalStatus.PENDING]
        if pending:
            parts.append("")
            parts.append(f"### 💡 {len(pending)} Proposals Pending")
            for p in pending[:5]:
                parts.append(f"- **{p.title}** — {p.expected_benefit}")
        critical_sectors = [
            (name, s) for name, s in health.get("sectors", {}).items()
            if s.get("status") in ("critical", "degraded")
        ]
        if critical_sectors:
            parts.append("")
            parts.append("### ⚠️ Areas Requiring Attention")
            for name, s in critical_sectors[:3]:
                parts.append(
                    f"- **{name}**: {s['status'].upper()} ({s['score']:.0f}%) — "
                    f"{s.get('reported_issue_records', s.get('issues', 0))} issue records reported; "
                    f"{s.get('non_nominal_observers', 0)} non-nominal of {s.get('observers', 0)} observers"
                )
        recommended_focus = [
            a for a in allocations if a.recommended_attention_pct > 15
        ]
        if critical_sectors and not recommended_focus:
            parts.append("")
            parts.append("### 📊 Recommended Focus")
            parts.append(
                "- Investigate degraded sector observations and attach source-backed "
                "issue details before declaring the platform issue-free."
            )
        elif recommended_focus:
            parts.append("")
            parts.append("### 📊 Recommended Focus")
            for a in recommended_focus[:3]:
                parts.append(f"- **{a.sector.value}**: {a.reasoning}")
        if outlook:
            parts.append("")
            parts.append(f"### 🔮 {outlook.horizon_days}-Day Outlook")
            parts.append(f"- Predicted health: {outlook.predicted_health_score:.0f}/100")
            if outlook.emerging_risks:
                parts.append(f"- ⚠️ Risks: {outlook.emerging_risks[0]}")
            if outlook.growth_opportunities:
                parts.append(f"- 📈 Opportunities: {outlook.growth_opportunities[0]}")
        recent = [t for t in list(self.history)[-5:] if t.status in (Status.COMPLETED, Status.VERIFIED)]
        if recent:
            parts.append("")
            parts.append("### ✅ Recent Actions")
            for t in recent:
                parts.append(f"- {t.description}")
        return "\n".join(parts)

    def correlate_observations(self) -> List[Dict[str, Any]]:
        correlations = []
        time_windows = defaultdict(list)
        for name, obs in self.observations.items():
            bucket = obs.timestamp.replace(second=0, microsecond=0)
            bucket = bucket - timedelta(minutes=bucket.minute % 5)
            time_windows[bucket].append((name, obs))
        for window_time, window_obs in time_windows.items():
            if len(window_obs) < 2:
                continue
            degraded = [
                name for name, obs in window_obs
                if obs.status in (Health.CRITICAL, Health.DEGRADED)
            ]
            if len(degraded) >= 2:
                correlations.append({
                    "timestamp": iso(window_time),
                    "engines": degraded,
                    "likely_cause": f"Shared infrastructure issue affecting {len(degraded)} engines",
                    "severity": "critical" if len(degraded) >= 3 else "warning",
                })
        return correlations

    def generate_initiatives(self) -> List[Dict[str, Any]]:
        initiatives = []
        health = self.health()
        if health["score"] >= 90:
            initiatives.append({
                "type": "optimization",
                "title": "Platform Performance Optimization Window",
                "description": "System health is excellent. Ideal time for proactive tuning.",
                "priority": "medium",
                "suggested_capabilities": ["optimize_infrastructure"],
            })
        used_capabilities = set(t.capability for t in self.history if t.status == Status.COMPLETED)
        unused = (
            set(self.capabilities.keys())
            - used_capabilities
            - {c for c in self.capabilities if self.capabilities[c].risk == Risk.CRITICAL}
        )
        if unused:
            initiatives.append({
                "type": "capability_gap",
                "title": f"Unused Capabilities Detected: {len(unused)}",
                "description": f"Capabilities never used: {', '.join(list(unused)[:5])}",
                "priority": "low",
                "unused_capabilities": list(unused),
            })
        return sorted(initiatives, key=lambda i: {"high": 0, "medium": 1, "low": 2}[i["priority"]])

    def briefing(self, kind: Briefing = Briefing.CHECKPOINT) -> dict:
        health = self.health()
        pending = [safe(asdict(t)) for t in self.tasks.values() if t.status == Status.APPROVAL]
        pending_props = [safe(asdict(p)) for p in self.proposals.values() if p.status == ProposalStatus.PENDING]
        failures = [t for t in self.history if t.status == Status.FAILED]
        brief = {
            "type": kind.value,
            "timestamp": iso(now()),
            "mood": (
                "critical" if health["score"] < 40
                else "attentive" if pending or pending_props
                else "watchful" if failures
                else "confident" if health["score"] >= 90
                else "focused"
            ),
            "health": health,
            "actions_taken": [
                t.description for t in list(self.history)[-10:]
                if t.status in (Status.COMPLETED, Status.VERIFIED)
            ],
            "pending_approvals": pending,
            "pending_proposals": pending_props,
            "insights": [
                f"Health score: {health['score']}",
                f"Pending approvals: {len(pending)}",
                f"Pending proposals: {len(pending_props)}",
                f"Recent failures: {len(failures)}",
            ],
            "plan_summary": {
                "tasks": len(self.tasks),
                "approvals": len(pending),
                "proposals": len(pending_props),
            },
        }
        self.briefings.append(brief)
        # Add a top-level `summary` so the event stream can render a
        # human-readable one-liner without knowing the brief's schema.
        brief["summary"] = (
            brief["insights"][0]
            if brief.get("insights")
            else f"Director briefing ({brief.get('type', 'unknown')})"
        )
        _publish_event("director_briefing", brief)
        return brief

    def _send_daily_briefing(self) -> None:
        try:
            from services.email_service import email_service
            health = self.health()
            tasks_done = sum(1 for t in self.history if t.status in (Status.COMPLETED, Status.VERIFIED))
            tasks_failed = sum(1 for t in self.history if t.status == Status.FAILED)
            pending_props = sum(1 for p in self.proposals.values() if p.status == ProposalStatus.PENDING)
            msg = (
                f"📊 <b>KANYOZA DAILY BRIEFING</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"✅ Health: {health['status'].upper()} ({health['score']:.0f}%)\n"
                f"📝 Tasks: {tasks_done} done, {tasks_failed} failed\n"
                f"💡 Proposals pending: {pending_props}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"👔 {len(self.observers)} engines | v{self.VERSION}"
            )
            email_service.send_telegram(msg)
            log.info("Daily briefing sent via Telegram")
        except Exception as exc:
            log.error("Daily briefing failed: %s", sanitize_error_message(exc))

    def _send_telegram(self, message: str) -> bool:
        try:
            from services.email_service import email_service
            return email_service.send_telegram(message)
        except Exception as exc:
            log.error("Telegram send failed: %s", sanitize_error_message(exc))
            return False

    def _record_decision(
        self,
        decision_text: str,
        reason: str,
        task: Task,
        outcome: str = None,
        stage: str = "execution",
    ) -> None:
        observed_state = []
        evidence_sources = []
        direct_sources = set(getattr(task, "sources", []) or [])
        for observer_name, observation in self.observations.items():
            source = str(getattr(observation, "source", observer_name) or observer_name)
            direct_match = (
                observer_name in direct_sources
                or source in direct_sources
                or f"engine_signal:{observer_name}" in direct_sources
                or f"engine_signal:{source}" in direct_sources
            )
            if direct_sources and not direct_match:
                continue
            stamp = getattr(observation, "timestamp", None)
            observed_at = iso(stamp) if isinstance(stamp, datetime) else None
            age_seconds = None
            freshness = "unknown"
            if isinstance(stamp, datetime):
                stamp_utc = stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)
                age_seconds = (datetime.now(timezone.utc) - stamp_utc).total_seconds()
                freshness = "fresh" if -30 <= age_seconds <= 300 else "stale"
            status = getattr(getattr(observation, "status", None), "value", getattr(observation, "status", None))
            evidence = {
                "observer": observer_name,
                "source": source,
                "sector": getattr(getattr(observation, "sector", None), "value", str(getattr(observation, "sector", "unknown"))),
                "status": str(status or "unknown").lower(),
                "observed_at": observed_at,
                "freshness": freshness,
                "age_seconds": round(age_seconds, 3) if age_seconds is not None else None,
                "relationship": "direct_source" if direct_match else "platform_context",
            }
            observed_state.append({
                "observer": observer_name,
                "sector": evidence["sector"],
                "reported_status": evidence["status"],
                "observed_at": observed_at,
                "freshness": freshness,
            })
            evidence_sources.append(evidence)

        fresh_evidence_count = sum(1 for item in evidence_sources if item["freshness"] == "fresh")
        evidence_state = (
            "unknown" if fresh_evidence_count == 0
            else "partial" if fresh_evidence_count < len(evidence_sources)
            else "present"
        )
        rationale = str(reason or "")
        d = Decision(
            id=f"decision_{uuid.uuid4().hex[:12]}",
            timestamp=now(),
            decision=decision_text,
            reason=rationale,
            confidence=task.confidence,
            sector=task.sector,
            authority=task.authority,
            risk=task.risk,
            task_id=task.id,
            outcome=outcome,
            stage=stage,
            observed_state=observed_state,
            inferences=([{
                "claim": rationale,
                "classification": "inference",
                "verification": "not_independently_verified",
            }] if rationale else []),
            assumptions=[],
            evidence_sources=evidence_sources,
            evidence_state=evidence_state,
            justification=rationale,
            action_taken={
                "task_id": task.id,
                "capability": task.capability,
                "task_status": getattr(task.status, "value", str(task.status)),
                "stage": stage,
                "outcome": outcome,
            },
            confidence_basis={
                "value_source": "legacy_task_confidence",
                "calibration": "not_evidence_calibrated",
                "evidence_source_count": len(evidence_sources),
                "fresh_evidence_source_count": fresh_evidence_count,
            },
        )
        self.decisions.append(d)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            log.debug("Director decision retained in memory; no running loop for persistence")
        else:
            loop.create_task(self.store.save_decision(d))

    def _learn(self, task: Task, signal: Optional[LearningSignal] = None) -> None:
        if signal is not None:
            entry = safe(asdict(signal))
        else:
            entry = {
                "id": f"learn_{uuid.uuid4().hex[:12]}",
                "task_id": task.id,
                "signal": "success" if task.status in (Status.COMPLETED, Status.VERIFIED) else "failure",
                "capability": task.capability,
                "sector": task.sector.value if hasattr(task.sector, "value") else str(task.sector),
                "duration_ms": 0.0,
                "error_type": task.error[:100] if task.error else None,
                "context": {},
                "timestamp": iso(now()),
                "lesson": f"Outcome of {task.capability}: {task.status.value}",
            }
        asyncio.create_task(self.store.save_learning(entry))

    def _drain_events(self) -> None:
        if not self._event_queue:
            return
        batch = []
        while self._event_queue:
            try:
                batch.append(self._event_queue.popleft())
            except IndexError:
                break
        for event in batch:
            try:
                if self._event_callback:
                    self._event_callback(event["name"], event["payload"])
            except Exception as exc:
                log.exception(
                    "Event processing failed: %s — %s",
                    event["name"], sanitize_error_message(exc),
                )

    def enqueue_event(self, name: str, payload: dict) -> None:
        self._event_queue.append({
            "name": name,
            "payload": payload,
            "timestamp": time.monotonic(),
        })

    def set_event_callback(self, cb: Callable) -> None:
        self._event_callback = cb

    def set_alert_callback(self, cb: Callable) -> None:
        self._alert_callback = cb

    def bind_capability(self, name: str, handler: Callable) -> None:
        if name not in self.capabilities:
            raise ValueError(f"Unknown capability: {name}")
        self.capabilities[name].handler = handler
        kind = "async" if inspect.iscoroutinefunction(handler) else "sync"
        log.info("Director: capability '%s' bound (%s)", name, kind)

    def register_observer(self, observer: FunctionObserver) -> None:
        self.observers[observer.name] = observer

    def register_capability(self, capability: Capability) -> None:
        if not capability.name or not capability.name.strip():
            raise ValueError("Capability name must be non-empty")
        if not callable(capability.handler):
            raise TypeError(f"Capability '{capability.name}' requires a callable handler")
        self.capabilities[capability.name] = capability

    @staticmethod
    def _handler_is_bound(handler: Any) -> bool:
        if not callable(handler):
            return False
        name = str(getattr(handler, "__name__", ""))
        return not name.startswith("placeholder_") and name != "<lambda>"

    @staticmethod
    def validate_capability_parameters(parameter_schema: Optional[dict], params: Any) -> List[str]:
        """Validate operator-supplied mission inputs against a closed object schema.

        This intentionally supports a small deterministic JSON Schema subset.
        Unsupported or open-ended schemas fail closed rather than guessing.
        """
        if not isinstance(parameter_schema, dict):
            return ["parameter_schema_missing"]
        if parameter_schema.get("type") != "object":
            return ["parameter_schema_must_be_object"]
        properties = parameter_schema.get("properties")
        if not isinstance(properties, dict):
            return ["parameter_schema_properties_invalid"]
        if parameter_schema.get("additionalProperties") is not False:
            return ["parameter_schema_must_close_object"]
        if not isinstance(params, dict):
            return ["parameters_must_be_object"]

        errors: List[str] = []
        required = parameter_schema.get("required", [])
        if not isinstance(required, list) or any(not isinstance(key, str) for key in required):
            return ["parameter_schema_required_invalid"]
        for key in required:
            if key not in properties:
                errors.append(f"schema_required_property_undefined:{key}")
            elif key not in params:
                errors.append(f"required_parameter_missing:{key}")

        for key, value in params.items():
            spec = properties.get(key)
            if not isinstance(spec, dict):
                errors.append(f"parameter_not_allowed:{key}")
                continue
            kind = spec.get("type")
            valid = (
                (kind == "string" and isinstance(value, str))
                or (kind == "boolean" and isinstance(value, bool))
                or (kind == "integer" and isinstance(value, int) and not isinstance(value, bool))
                or (kind == "number" and isinstance(value, (int, float)) and not isinstance(value, bool))
                or (kind == "array" and isinstance(value, list))
                or (kind == "object" and isinstance(value, dict))
            )
            if not valid:
                errors.append(f"parameter_type_invalid:{key}")
                continue
            if "enum" in spec and value not in spec["enum"]:
                errors.append(f"parameter_value_not_allowed:{key}")
            if isinstance(value, str):
                if len(value) < int(spec.get("minLength", 0)):
                    errors.append(f"parameter_too_short:{key}")
                if len(value) > int(spec.get("maxLength", 10000)):
                    errors.append(f"parameter_too_long:{key}")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if "minimum" in spec and value < spec["minimum"]:
                    errors.append(f"parameter_below_minimum:{key}")
                if "maximum" in spec and value > spec["maximum"]:
                    errors.append(f"parameter_above_maximum:{key}")
        return errors

    def capability_catalog(self) -> Dict[str, Dict[str, Any]]:
        catalog: Dict[str, Dict[str, Any]] = {}
        for name, cap in sorted(self.capabilities.items()):
            handler = getattr(cap, "handler", None)
            handler_bound = self._handler_is_bound(handler)
            handler_name = str(getattr(handler, "__name__", "")) if handler else ""
            intentionally_blocked = handler_name == "_blocked"
            handoff = getattr(cap, "governed_handoff", None)
            governed_bound = callable(handoff)
            effective_authority = self.policy.authority(cap)
            approval_required = self.policy.requires_approval(cap)
            verification_bound = callable(getattr(cap, "outcome_predicate", None))
            verification_window = int(getattr(cap, "verification_window_seconds", 0) or 0)
            issues = []
            if not cap.enabled:
                issues.append("disabled")
            if not handler_bound:
                issues.append("production_handler_missing")
            if intentionally_blocked:
                issues.append("safety_blocked_handler")
            if cap.proposal_capable and not governed_bound:
                issues.append("governed_handoff_missing")
            if verification_window and not verification_bound:
                issues.append("outcome_verification_missing")
            if cap.risk == Risk.CRITICAL and effective_authority == Authority.AUTO:
                issues.append("critical_risk_auto_authority")

            if cap.proposal_capable:
                execution_path = "proposal_governed_handoff"
                execution_boundary = "canonical_action_kernel_receipt_required"
            elif intentionally_blocked:
                execution_path = "safety_blocked"
                execution_boundary = "human_review_and_explicit_safe_handler_required"
            else:
                execution_path = "director_task_handler"
                execution_boundary = "policy_approval_and_authority_fence"

            implementation_ready = handler_bound and not intentionally_blocked
            ready = bool(
                cap.enabled and implementation_ready
                and (not cap.proposal_capable or governed_bound)
                and (not verification_window or verification_bound)
                and not any(issue in issues for issue in (
                    "critical_risk_auto_authority", "production_handler_missing",
                    "safety_blocked_handler", "governed_handoff_missing",
                    "outcome_verification_missing", "disabled",
                ))
            )
            binding_status = self.capability_binding_report.get(name, "not_run")
            catalog[name] = {
                "name": name,
                "definition_source": "built_in_registry",
                "catalog_type": "predefined",
                "sector": cap.sector.value,
                "description": cap.description,
                "enabled": bool(cap.enabled),
                "authority": cap.authority.value,
                "effective_authority": effective_authority.value,
                "approval_required": bool(approval_required),
                "authorization_requirements": {
                    "capability_catalog_api": "authenticated_request",
                    "direct_execute_api": "authenticated_write_request_and_platform_scope",
                    "director_policy_approval": bool(approval_required),
                    "proposal_approval": bool(cap.proposal_capable),
                    "authority_fence": "enforced_when_configured",
                },
                "risk": cap.risk.value,
                "target_param": cap.target_param,
                "parameter_schema": cap.parameter_schema,
                "parameter_contract_bound": isinstance(cap.parameter_schema, dict),
                "workspace_target_bound": bool(cap.target_param),
                "mission_staging_mode": str(getattr(cap, "mission_staging_mode", "disabled")),
                "mission_staging_ready": bool(
                    ready
                    and getattr(cap, "mission_staging_mode", "disabled") == "read_only_review"
                    and bool(cap.target_param)
                    and isinstance(cap.parameter_schema, dict)
                    and cap.parameter_schema.get("type") == "object"
                    and isinstance(cap.parameter_schema.get("properties"), dict)
                    and cap.parameter_schema.get("additionalProperties") is False
                ),
                "proposal_capable": bool(cap.proposal_capable),
                "handler_bound": handler_bound,
                "handler_name": handler_name or None,
                "binding_status": binding_status,
                "binding_reported_at": self.capability_binding_at,
                "runtime_binding_confirmed": binding_status in {"bound", "bound_governed_handoff"},
                "runtime_dependencies_verified": False,
                "readiness_scope": "static_wiring_and_contracts_only",
                "implementation_ready": implementation_ready,
                "governed_handoff_bound": governed_bound,
                "execution_path": execution_path,
                "execution_boundary": execution_boundary,
                "has_verification": verification_bound,
                "verification_window_seconds": verification_window,
                "ready": ready,
                "issues": issues,
            }
        return catalog

    def _init_goals(self) -> None:
        for gid, name, sector, pri in [
            ("system_stability", "System stability", Sector.HEALTH, 100),
            ("security", "Security posture", Sector.SECURITY, 100),
            ("clients", "Client commitments", Sector.CLIENTS, 90),
            ("brands", "Brand health", Sector.BRANDS, 85),
            ("content", "Content consistency", Sector.CONTENT, 80),
            ("growth", "Business growth", Sector.GROWTH, 70),
            ("learning", "Continuous learning", Sector.LEARNING, 60),
        ]:
            self.goals[gid] = Goal(gid, name, name, sector, pri)

    def _init_capabilities(self) -> None:
        for name, sector, desc, auth, risk, target_param in [
            ("optimize_content_schedule", Sector.CONTENT, "Adjust safe scheduling", Authority.NOTIFY, Risk.LOW, "brand_id"),
            ("review_content_quality", Sector.CONTENT, "Review measured low-quality content", Authority.NOTIFY, Risk.LOW, "brand_id"),
            ("calculate_brand_health", Sector.BRANDS, "Calculate brand health", Authority.AUTO, Risk.LOW, "brand_id"),
            ("queue_brand_revival", Sector.BRANDS, "Queue bounded revival", Authority.NOTIFY, Risk.MEDIUM, "brand_id"),
            ("run_health_scan", Sector.HEALTH, "Run platform health scan", Authority.AUTO, Risk.LOW, None),
            ("run_guardian_scan", Sector.SECURITY, "Run security scan", Authority.AUTO, Risk.LOW, None),
            ("quarantine_security_issue", Sector.SECURITY, "Quarantine confirmed issue", Authority.NOTIFY, Risk.HIGH, "issue_id"),
            ("prepare_security_remediation", Sector.SECURITY, "Prepare security remediation", Authority.SUGGEST, Risk.HIGH, "issue_id"),
            ("generate_client_report", Sector.CLIENTS, "Generate client report", Authority.AUTO, Risk.LOW, "client_id"),
            ("prepare_client_message", Sector.CLIENTS, "Prepare client message", Authority.SUGGEST, Risk.MEDIUM, "client_id"),
            ("analyze_growth_opportunity", Sector.GROWTH, "Analyze growth", Authority.AUTO, Risk.LOW, "brand_id"),
            ("optimize_infrastructure", Sector.INFRA, "Prepare infrastructure optimization", Authority.SUGGEST, Risk.HIGH, "component"),
            ("run_learning_cycle", Sector.LEARNING, "Learn from outcomes", Authority.AUTO, Risk.LOW, None),
            ("self_diagnostic", Sector.SELF, "Diagnose Director", Authority.AUTO, Risk.LOW, None),
            ("web_search", Sector.LEARNING, "Search the live web", Authority.AUTO, Risk.LOW, None),
            ("web_fetch", Sector.LEARNING, "Fetch a web page", Authority.AUTO, Risk.LOW, None),
            ("ask_with_tools", Sector.LEARNING, "Answer using web tools when needed", Authority.AUTO, Risk.LOW, None),
        ]:
            self.capabilities[name] = Capability(
                name, sector, desc, self._placeholder(name), auth, risk,
                target_param=target_param,
            )

        # Explicit contracts for bounded diagnostics and evidence collection.
        # Mutating, proposal-backed, placeholder, and externally scoped
        # capabilities remain without a contract until their full execution
        # boundary is proven.
        self.capabilities["run_health_scan"].parameter_schema = {
            "type": "object", "properties": {}, "required": [], "additionalProperties": False,
        }
        self.capabilities["run_guardian_scan"].parameter_schema = {
            "type": "object", "properties": {}, "required": [], "additionalProperties": False,
        }
        self.capabilities["self_diagnostic"].parameter_schema = {
            "type": "object", "properties": {}, "required": [], "additionalProperties": False,
        }
        self.capabilities["calculate_brand_health"].parameter_schema = {
            "type": "object",
            "properties": {"brand_id": {"type": "string", "minLength": 1, "maxLength": 128}},
            "required": ["brand_id"],
            "additionalProperties": False,
        }
        self.capabilities["review_content_quality"].parameter_schema = {
            "type": "object",
            "properties": {"brand_id": {"type": "string", "minLength": 1, "maxLength": 128}},
            "required": ["brand_id"],
            "additionalProperties": False,
        }
        self.capabilities["calculate_brand_health"].mission_staging_mode = "read_only_review"
        self.capabilities["review_content_quality"].mission_staging_mode = "read_only_review"
        self.capabilities["restart_failed_service"] = Capability(
            name="restart_failed_service",
            sector=Sector.HEALTH,
            description="Restart isolated failed service",
            handler=self._placeholder("restart_failed_service"),
            authority=Authority.AUTO,
            risk=Risk.MEDIUM,
            target_param="service_name",
            outcome_predicate=_verify_restart,
            verification_window_seconds=60,
            proposal_capable=False,
        )

        self.capabilities["update_brand_posting_schedule"] = Capability(
            name="update_brand_posting_schedule",
            sector=Sector.CONTENT,
            description="Update a brand's posting hours",
            handler=self._placeholder("update_brand_posting_schedule"),
            authority=Authority.SUGGEST,
            risk=Risk.LOW,
            target_param="brand_id",
            proposal_capable=True,
            verification_window_seconds=1,
            outcome_predicate=_verify_schedule_change,
        )

        self.capabilities["scale_worker_pool"] = Capability(
            name="scale_worker_pool",
            sector=Sector.INFRA,
            description="Scale a worker pool",
            handler=self._placeholder("scale_worker_pool"),
            authority=Authority.SUGGEST,
            risk=Risk.LOW,
            target_param="pool_name",
            proposal_capable=True,
            verification_window_seconds=300,
            outcome_predicate=_verify_worker_scale,
        )

        for name, sector in [
            ("delete_brand", Sector.BRANDS),
            ("delete_client_data", Sector.CLIENTS),
            ("drop_database", Sector.INFRA),
            ("reset_database", Sector.INFRA),
            ("rotate_credentials", Sector.SECURITY),
            ("change_production_secrets", Sector.SECURITY),
            ("disable_security_controls", Sector.SECURITY),
            ("change_billing", Sector.FINANCE),
            ("refund_client", Sector.FINANCE),
            ("send_legal_notice", Sector.CLIENTS),
            ("deploy_breaking_change", Sector.INFRA),
            ("change_access_control", Sector.SECURITY),
        ]:
            self.capabilities[name] = Capability(
                name, sector, name.replace("_", " ").title(),
                self._blocked, Authority.SUGGEST, Risk.CRITICAL,
                target_param=None,
            )

    def _placeholder(self, name: str) -> Callable:
        def _unbound(p: dict) -> dict:
            return {
                "ok": False,
                "status": "unavailable",
                "capability": name,
                "error": "handler_not_bound",
                "message": (
                    f"Capability '{name}' has no bound production handler. "
                    "Bind via director_bindings before autonomous execution."
                ),
                "params": p or {},
            }
        _unbound.__name__ = f"placeholder_{name}"
        return _unbound

    def _blocked(self, p: dict) -> dict:
        return {
            "ok": False,
            "status": "blocked",
            "error": "critical_requires_approval",
            "reason": "Critical action requires human approval and a bound production handler.",
        }

    @staticmethod
    def _handler_result_ok(result) -> bool:
        if result is None:
            return False
        if not isinstance(result, dict):
            return True
        if result.get("ok") is False:
            return False
        st = str(result.get("status") or "").lower()
        if st in (
            "delegated", "unavailable", "blocked", "failed",
            "error", "unbound", "not_implemented",
        ):
            return False
        if result.get("error") and result.get("ok") is not True:
            return False
        return True


__all__ = [
    "Director",
    "auto_discover_engines",
]
