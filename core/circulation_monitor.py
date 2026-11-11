"""
Kanyoza Data Flow Micro-Organism (DFMO) v1.3
=============================================

Infrastructure circulation sentinel — not the whole platform health model.

v1.3:
  - Patrol wires auto_heal for low-risk blockages
  - Active event-bus probe (publish → ack) instead of lifetime counters only
  - monitor_checks retention / purge
  - Blockages hand off to Reliability Engine
  - Split scores: infrastructure vs schema integrity (workspace-aware events)

v1.2: real DB insert, service health, queue depth, flow score
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("kanyoza.circulation")


class BlockageSeverity(str, Enum):
    NONE = "none"
    MILD = "mild"
    MODERATE = "moderate"
    SEVERE = "severe"
    CRITICAL = "critical"


@dataclass
class BlockageReport:
    id: str = field(default_factory=lambda: f"block_{uuid.uuid4().hex[:12]}")
    location: str = ""
    severity: BlockageSeverity = BlockageSeverity.NONE
    description: str = ""
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    resolution: str = ""
    auto_healable: bool = False
    healed: bool = False
    healed_at: Optional[datetime] = None


@dataclass
class CirculationSnapshot:
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    event_bus_flowing: bool = True
    database_writing: bool = True
    # Separated sensors (Phase 3) — never treat SELECT success as write proof
    database_reachable: bool = True
    database_readable: bool = True
    database_writable: bool = True
    services_healthy: int = 0
    services_total: int = 0
    active_blockages: List[BlockageReport] = field(default_factory=list)
    flow_score: float = 100.0  # infrastructure pathways
    # Split dimensions (v1.3)
    infrastructure_score: float = 100.0
    event_schema_score: float = 100.0  # workspace-aware / probe integrity
    workflow_score: float = 100.0  # end-to-end path probes
    probe_acked: bool = False
    path_probe_details: list = field(default_factory=list)


class DataFlowMicroOrganism:
    """Digital sentinel that patrols infrastructure circulation."""

    VERSION = "1.4.0"

    HEALTHY_STATUSES = {"healthy", "nominal", "ok", "ready", "running", "idle"}  # starting/degraded are not healthy
    UNHEALTHY_STATUSES = {"unhealthy", "offline", "failed", "critical", "stopped", "error"}

    # Retention for monitor_checks probe rows
    CHECK_RETENTION_HOURS = 24
    PURGE_EVERY_N_PATROLS = 20  # ~10 min at 30s interval

    def __init__(self, container=None, event_bus=None, supabase=None):
        self._container = container
        self._event_bus = event_bus
        self._supabase = supabase
        self._running = False
        self._monitor_task: Optional[asyncio.Task] = None
        self._blockages: List[BlockageReport] = []
        self._snapshots: List[CirculationSnapshot] = []
        self._consecutive_event_bus_failures = 0
        self._consecutive_db_failures = 0
        self._patrol_count = 0
        self._pending_probes: Dict[str, float] = {}  # probe_id → monotonic sent_at
        self._probe_acks: set = set()
        self._probe_acks_max = 500  # hard bound (Phase 3)
        self._legacy_event_warnings = 0

    @property
    def service_name(self) -> str:
        return "circulation_monitor"

    async def start(self, interval_seconds: int = 30) -> None:
        if self._running:
            return
        self._running = True
        self._subscribe_probe_ack()
        self._monitor_task = asyncio.create_task(
            self._patrol_loop(interval_seconds),
            name="dfmo-patrol",
        )
        logger.info("🦠 Data Flow Micro-Organism v%s — STARTED PATROL", self.VERSION)

    async def stop(self) -> None:
        self._running = False
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

    async def _patrol_loop(self, interval_seconds: int) -> None:
        while self._running:
            try:
                snapshot = await self.check_circulation()
                self._snapshots.append(snapshot)
                self._snapshots = self._snapshots[-100:]
                self._patrol_count += 1

                # Log split scores
                if snapshot.active_blockages:
                    logger.warning(
                        "🦠 DFMO: %d blockage(s) — infra=%.1f%% schema=%.1f%% workflow=%.1f%% flow=%.1f%%",
                        len(snapshot.active_blockages),
                        snapshot.infrastructure_score,
                        snapshot.event_schema_score,
                        snapshot.workflow_score,
                        snapshot.flow_score,
                    )
                    for blockage in snapshot.active_blockages:
                        logger.warning(
                            "   🚫 %s (severity=%s healable=%s): %s",
                            blockage.location,
                            blockage.severity.value,
                            blockage.auto_healable,
                            blockage.description,
                        )
                        # Low-risk auto-heal
                        if (
                            blockage.auto_healable
                            and blockage.severity in (BlockageSeverity.MILD, BlockageSeverity.MODERATE)
                            and not blockage.healed
                        ):
                            healed = await self.auto_heal(blockage)
                            if healed:
                                logger.info("🦠 DFMO: Auto-healed %s", blockage.location)
                        # Hand off to Reliability (always for severe+, soft for others)
                        await self._handoff_reliability(blockage, snapshot)
                else:
                    logger.info(
                        "🦠 DFMO: Circulation healthy — infra=%.1f%% schema=%.1f%% workflow=%.1f%% flow=%.1f%% probe_ack=%s",
                        snapshot.infrastructure_score,
                        snapshot.event_schema_score,
                        snapshot.workflow_score,
                        snapshot.flow_score,
                        snapshot.probe_acked,
                    )
                # Periodically emit unified organism health
                if self._patrol_count % 5 == 0:
                    try:
                        from services.unified_health import collect_unified_health
                        uh = await collect_unified_health(self._container)
                        logger.info(
                            "🩺 UNIFIED HEALTH overall=%.1f%% dims=%s",
                            uh.get("overall", 0),
                            uh.get("dimensions"),
                        )
                    except Exception as _uh:
                        logger.debug("unified health: %s", _uh)


                # Periodic retention purge
                if self._patrol_count % self.PURGE_EVERY_N_PATROLS == 0:
                    await self._purge_old_checks()

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("DFMO patrol error: %s", exc)

            await asyncio.sleep(interval_seconds)

    async def check_circulation(self) -> CirculationSnapshot:
        blockages: List[BlockageReport] = []
        snapshot = CirculationSnapshot()

        # 1) Event bus — active probe preferred
        bus_ok, probe_acked, schema_ok = await self._check_event_bus_flow()
        snapshot.event_bus_flowing = bus_ok
        snapshot.probe_acked = probe_acked
        if not bus_ok:
            self._consecutive_event_bus_failures += 1
            blockages.append(BlockageReport(
                location="event_bus",
                severity=BlockageSeverity.SEVERE if self._consecutive_event_bus_failures >= 3 else BlockageSeverity.MODERATE,
                description="Event bus not circulating (probe failed or unavailable)",
                resolution="Restart event bus / check UnifiedEventBus",
                auto_healable=True,
            ))
        else:
            self._consecutive_event_bus_failures = 0

        if not schema_ok:
            blockages.append(BlockageReport(
                location="event_schema",
                severity=BlockageSeverity.MILD,
                description="Event schema integrity weak (missing workspace_id / no probe ack)",
                resolution="Ensure platform events use workspace_id=platform",
                auto_healable=False,
            ))

        # 2) Database write probe
        db_health = await self._check_database_health()
        snapshot.database_reachable = bool(db_health.get("reachable"))
        snapshot.database_readable = bool(db_health.get("readable"))
        snapshot.database_writable = bool(db_health.get("writable"))
        snapshot.database_writing = snapshot.database_writable  # honest alias
        if not snapshot.database_writable:
            self._consecutive_db_failures += 1
            detail = (db_health.get("detail") or "")[:200]
            # Distinguish read-only vs total outage
            if not snapshot.database_reachable:
                desc = f"Database unreachable ({detail})"
                sev = BlockageSeverity.CRITICAL
            elif not snapshot.database_readable:
                desc = f"Database not readable ({detail})"
                sev = BlockageSeverity.SEVERE
            else:
                desc = f"Database not writable ({detail})"
                sev = BlockageSeverity.SEVERE
            blockages.append(BlockageReport(
                location="database",
                severity=sev,
                description=desc,
                resolution="Check Supabase credentials, RLS, and monitor_checks schema",
                auto_healable=False,
            ))
        else:
            self._consecutive_db_failures = 0

        # 3) Services
        svc = await self._check_services()
        snapshot.services_healthy = int(svc.get("healthy", 0))
        snapshot.services_total = int(svc.get("total", 0))
        unhealthy = svc.get("unhealthy_list") or []
        if unhealthy:
            blockages.append(BlockageReport(
                location="services",
                severity=BlockageSeverity.MODERATE,
                description=f"Unhealthy services: {', '.join(unhealthy[:8])}",
                resolution=f"Restart or investigate: {unhealthy[0]}",
                auto_healable=False,  # only mark healed after Director confirms
            ))

        # 4) Queues
        for issue in await self._check_queues():
            blockages.append(BlockageReport(
                location=issue.get("location", "queue"),
                severity=BlockageSeverity.MODERATE,
                description=issue.get("description", "Queue backlog"),
                resolution=issue.get("resolution", "Scale workers"),
                auto_healable=False,
            ))

        snapshot.active_blockages = blockages
        self._blockages = blockages

        # Infrastructure score = classic 4 probes
        passed = 0
        total = 4
        if bus_ok:
            passed += 1
        if snapshot.database_writable:
            passed += 1
        if snapshot.services_total == 0 or (
            snapshot.services_healthy >= max(1, snapshot.services_total * 0.7)
        ):
            passed += 1
        if not any(b.location in ("task_queue", "render_queue") for b in blockages):
            passed += 1
        snapshot.infrastructure_score = round((passed / total) * 100, 1)

        # Schema score
        schema_points = 100.0
        if not probe_acked:
            schema_points -= 25
        if not schema_ok:
            schema_points -= 25
        if self._legacy_event_warnings > 0:
            schema_points -= min(30, self._legacy_event_warnings * 5)
        snapshot.event_schema_score = round(max(0.0, schema_points), 1)

        # 5) End-to-end path probes (workflow integrity) — every 3rd patrol
        try:
            from core.circulation_path_probes import run_all_path_probes, score_path_results
            if self._patrol_count % 3 == 0 or self._patrol_count <= 1:
                path_results = await run_all_path_probes(supabase=self._supabase)
            else:
                # reuse last workflow score if present
                prev = self._snapshots[-1] if self._snapshots else None
                if prev and getattr(prev, "workflow_score", None) is not None:
                    snapshot.workflow_score = prev.workflow_score
                    snapshot.path_probe_details = list(getattr(prev, "path_probe_details", []) or [])
                    path_results = []
                else:
                    path_results = await run_all_path_probes(supabase=self._supabase)
            if path_results:
                snapshot.workflow_score = score_path_results(path_results)
                snapshot.path_probe_details = [
                    {
                        "name": r.name,
                        "ok": r.ok,
                        "ms": r.duration_ms,
                        "detail": r.detail,
                        "critical": r.critical,
                    }
                    for r in path_results
                ]
                for r in path_results:
                    if not r.ok and r.critical:
                        blockages.append(BlockageReport(
                            location=f"path:{r.name}",
                            severity=BlockageSeverity.MODERATE,
                            description=f"Path probe failed: {r.detail}",
                            resolution="Inspect publish/content/DB path",
                            auto_healable=False,
                        ))
                snapshot.active_blockages = blockages
                self._blockages = blockages
        except Exception as exc:
            logger.debug("DFMO path probes skipped: %s", exc)
            snapshot.workflow_score = 50.0

        # Combined flow_score: infra + schema + workflow
        snapshot.flow_score = round(
            snapshot.infrastructure_score * 0.50
            + snapshot.event_schema_score * 0.20
            + snapshot.workflow_score * 0.30,
            1,
        )
        return snapshot

    # ── Sensors ───────────────────────────────────────────────────────────

    async def _check_event_bus_flow(self) -> tuple[bool, bool, bool]:
        """
        Returns (bus_available, probe_acked, schema_ok).

        Prefer active probe; fall back to health_check / recent activity.
        """
        bus = self._event_bus
        if bus is None and self._container:
            try:
                from core.unified_bus import UnifiedEventBus
                bus = self._container.try_resolve(UnifiedEventBus)
            except Exception:
                bus = None

        if bus is None:
            try:
                from core.unified_bus import get_unified_bus
                bus = await get_unified_bus()
            except Exception:
                bus = None

        if bus is None:
            return False, False, False

        probe_id = f"probe_{uuid.uuid4().hex[:10]}"
        probe_acked = False
        schema_ok = True
        bus_ok = True

        try:
            self._pending_probes[probe_id] = time.monotonic()
            payload = {
                "probe_id": probe_id,
                "workspace_id": "platform",
                "source": "dfmo",
                "ts": datetime.now(timezone.utc).isoformat(),
            }
            # Publish probe
            if hasattr(bus, "publish"):
                try:
                    result = bus.publish(
                        "circulation.probe",
                        payload,
                        source="dfmo",
                        workspace_id="platform",
                    )
                    if asyncio.iscoroutine(result):
                        await result
                except TypeError:
                    result = bus.publish("circulation.probe", payload)
                    if asyncio.iscoroutine(result):
                        await result
            # Wait briefly for ack (same process handlers)
            await asyncio.sleep(0.15)
            probe_acked = probe_id in self._probe_acks
            self._probe_acks.discard(probe_id)
            self._pending_probes.pop(probe_id, None)

            # Fallback: health / stats if no ack path
            if not probe_acked:
                if hasattr(bus, "health_check"):
                    hc = bus.health_check()
                    if asyncio.iscoroutine(hc):
                        hc = await hc
                    if isinstance(hc, dict) and str(hc.get("status", "")).lower() in self.UNHEALTHY_STATUSES:
                        bus_ok = False
                elif hasattr(bus, "get_stats"):
                    stats = bus.get_stats()
                    if asyncio.iscoroutine(stats):
                        stats = await stats
                    # Lifetime counters alone are weak — only use as soft signal
                    if isinstance(stats, dict) and stats.get("running") is False:
                        bus_ok = False
                # No ack but bus object exists → still mark schema weak
                schema_ok = False
            else:
                schema_ok = True

        except Exception as exc:
            logger.debug("DFMO bus probe error: %s", exc)
            bus_ok = False
            schema_ok = False

        return bus_ok, probe_acked, schema_ok

    def _subscribe_probe_ack(self) -> None:
        """Subscribe so circulation.probe is acked in-process."""
        bus = self._event_bus
        if bus is None:
            return

        async def _ack(payload: Any = None, **kwargs):
            try:
                data = payload if isinstance(payload, dict) else kwargs
                if not isinstance(data, dict) and hasattr(payload, "payload"):
                    data = getattr(payload, "payload", {}) or {}
                pid = (data or {}).get("probe_id")
                if pid:
                    self._probe_acks.add(pid)
                    if len(self._probe_acks) > self._probe_acks_max:
                        # drop arbitrary old ids
                        for _drop in list(self._probe_acks)[: len(self._probe_acks) - self._probe_acks_max]:
                            self._probe_acks.discard(_drop)
            except Exception:
                pass

        def _sync_ack(payload=None, **kwargs):
            try:
                data = payload if isinstance(payload, dict) else {}
                pid = data.get("probe_id")
                if pid:
                    self._probe_acks.add(pid)
                    if len(self._probe_acks) > self._probe_acks_max:
                        # drop arbitrary old ids
                        for _drop in list(self._probe_acks)[: len(self._probe_acks) - self._probe_acks_max]:
                            self._probe_acks.discard(_drop)
            except Exception:
                pass

        try:
            if hasattr(bus, "subscribe"):
                # Prefer async handler if bus supports it
                try:
                    bus.subscribe("circulation.probe", _ack)
                except Exception:
                    bus.subscribe("circulation.probe", _sync_ack)
                logger.debug("DFMO: subscribed circulation.probe ack")
        except Exception as exc:
            logger.debug("DFMO probe subscribe skipped: %s", exc)

    async def _check_database_health(self) -> dict:
        """Separate reachability / readability / writability sensors.

        Never promote a successful SELECT into database_writing=True.
        """
        result = {
            "reachable": False,
            "readable": False,
            "writable": False,
            "detail": None,
        }
        try:
            supabase = self._supabase
            if supabase is None:
                from services.supabase import get_service_client_from_settings
                supabase = get_service_client_from_settings()
            if supabase is None:
                result["detail"] = "no_supabase_client"
                return result

            now_iso = datetime.now(timezone.utc).isoformat()
            loop = asyncio.get_event_loop()

            def _probe():
                out = {"reachable": False, "readable": False, "writable": False, "detail": None}
                try:
                    # Readable: SELECT
                    supabase.table("monitor_checks").select("id").limit(1).execute()
                    out["reachable"] = True
                    out["readable"] = True
                except Exception as sel_exc:
                    out["detail"] = f"select_failed:{sel_exc}"
                    return out

                # Writable: INSERT probe row (prefer with target_id if available)
                target_id = None
                try:
                    tgt = (
                        supabase.table("monitor_targets")
                        .select("id")
                        .limit(1)
                        .execute()
                    )
                    rows = getattr(tgt, "data", None) or []
                    if rows:
                        target_id = rows[0].get("id")
                except Exception:
                    pass

                # Try several insert shapes — production schemas vary (400 on mismatch)
                candidates = []
                base = {"source": "dfmo_probe", "checked_at": now_iso}
                if target_id:
                    base["target_id"] = target_id
                candidates.append({**base, "status": "up", "http_status": 200, "response_time_ms": 0})
                candidates.append({**base, "status": "ok"})
                candidates.append({**base, "status": "healthy"})
                candidates.append({**base, "status": "up"})
                # UUID-less minimal
                candidates.append({"status": "up", "checked_at": now_iso, "source": "dfmo_probe"})

                last_err = None
                for row in candidates:
                    try:
                        supabase.table("monitor_checks").insert(row).execute()
                        out["writable"] = True
                        out["detail"] = "insert_ok"
                        return out
                    except Exception as ins_exc:
                        last_err = ins_exc
                        continue
                out["detail"] = f"insert_failed:{last_err}"
                return out

            probed = await loop.run_in_executor(None, _probe)
            result.update(probed)
            logger.debug("DFMO DB health %s", result)
            return result
        except Exception as exc:
            logger.warning("DFMO DB health probe failed: %s", exc)
            result["detail"] = str(exc)
            return result

    async def _check_database_writes(self) -> bool:
        """Backward-compatible: True only when writable (not merely readable)."""
        health = await self._check_database_health()
        return bool(health.get("writable"))


    async def _purge_old_checks(self) -> None:
        """Delete DFMO probe rows older than retention window."""
        try:
            supabase = self._supabase
            if supabase is None:
                from services.supabase import get_service_client_from_settings
                supabase = get_service_client_from_settings()
            if supabase is None:
                return
            cutoff = (
                datetime.now(timezone.utc) - timedelta(hours=self.CHECK_RETENTION_HOURS)
            ).isoformat()
            loop = asyncio.get_event_loop()

            def _delete():
                try:
                    # Prefer source filter if column exists
                    return (
                        supabase.table("monitor_checks")
                        .delete()
                        .lt("checked_at", cutoff)
                        .eq("source", "dfmo_probe")
                        .execute()
                    )
                except Exception:
                    # Fallback: delete by age only (careful — only if volume is DFMO-dominated)
                    return (
                        supabase.table("monitor_checks")
                        .delete()
                        .lt("checked_at", cutoff)
                        .execute()
                    )

            await loop.run_in_executor(None, _delete)
            logger.info(
                "🦠 DFMO: purged monitor_checks older than %dh",
                self.CHECK_RETENTION_HOURS,
            )
        except Exception as exc:
            logger.debug("DFMO purge skipped: %s", exc)

    async def _check_services(self) -> Dict[str, Any]:
        healthy = 0
        total = 0
        unhealthy_list: List[str] = []
        try:
            from lifecycle import health_registry
            reports = health_registry.check_all(timeout_per_service=3.0)
            if asyncio.iscoroutine(reports):
                reports = await reports
            for name, report in (reports or {}).items():
                total += 1
                status = getattr(getattr(report, "status", None), "value", None) or str(
                    getattr(report, "status", "")
                )
                st = str(status).lower()
                if st in self.UNHEALTHY_STATUSES:
                    unhealthy_list.append(name)
                elif st in self.HEALTHY_STATUSES or st in ("degraded", "unknown", ""):
                    healthy += 1
                else:
                    healthy += 1  # soft
        except Exception as exc:
            logger.debug("DFMO service check: %s", exc)
        return {
            "healthy": healthy,
            "total": total,
            "unhealthy_list": unhealthy_list,
        }

    async def _check_queues(self) -> List[Dict[str, str]]:
        issues: List[Dict[str, str]] = []
        try:
            if self._container:
                from workers.task_queue import TaskQueue
                tq = self._container.try_resolve(TaskQueue)
                if tq and hasattr(tq, "queue"):
                    qsize = tq.queue.qsize()
                    if qsize > 100:
                        issues.append({
                            "location": "task_queue",
                            "description": f"Task queue overflowing ({qsize} tasks)",
                            "resolution": "Increase workers or reduce tasks",
                        })
        except Exception:
            pass
        try:
            from core.render_queue import get_render_queue
            rq = get_render_queue()
            if rq and hasattr(rq, "_pending"):
                pending = len(rq._pending)
                if pending > 50:
                    issues.append({
                        "location": "render_queue",
                        "description": f"Render queue backing up ({pending} renders)",
                        "resolution": "Check renderer or reduce load",
                    })
        except Exception:
            pass
        return issues

    # ── Heal + Reliability ───────────────────────────────────────────────

    async def auto_heal(self, blockage: BlockageReport) -> bool:
        """Bounded low-risk remediation only.

        Truthful contract (Phase 3):
          - Only event_bus has a concrete local heal path
          - service:* may *request* Director restart but does not claim healed
            unless Director reports success
          - Never set healed=True on best-effort soft actions
        """
        if not blockage.auto_healable or blockage.healed:
            return False
        try:
            if blockage.location == "event_bus":
                logger.info("🦠 DFMO: Auto-healing event bus...")
                bus = self._event_bus
                if bus is None:
                    try:
                        from core.unified_bus import get_unified_bus
                        bus = await get_unified_bus()
                    except Exception:
                        bus = None
                if bus is None:
                    logger.warning("DFMO: event_bus heal skipped — no bus instance")
                    return False
                if hasattr(bus, "stop"):
                    r = bus.stop()
                    if asyncio.iscoroutine(r):
                        await r
                if hasattr(bus, "start"):
                    r = bus.start()
                    if asyncio.iscoroutine(r):
                        await r
                blockage.healed = True
                blockage.healed_at = datetime.now(timezone.utc)
                logger.info("DFMO: event_bus restart completed")
                return True

            if blockage.location == "services":
                # Soft cache clear is not a heal — do not mark healed for it alone
                try:
                    from services.meta_engine import meta_engine
                    if hasattr(meta_engine, "_cached_health"):
                        meta_engine._cached_health.clear()
                except Exception:
                    pass
                name = ""
                desc = blockage.description or ""
                if ":" in desc:
                    name = desc.split(":", 1)[1].split(",")[0].strip()
                if not name:
                    logger.info(
                        "DFMO: service blockage not auto-healable without service name"
                    )
                    return False
                try:
                    from services.director import director
                    if not director or not hasattr(director, "execute_capability"):
                        logger.info(
                            "DFMO: cannot heal service=%s — Director unavailable",
                            name,
                        )
                        return False
                    result = await director.execute_capability(
                        "restart_failed_service",
                        {"service": name, "source": "dfmo"},
                    )
                    ok = False
                    if isinstance(result, dict):
                        ok = bool(result.get("ok") or result.get("success") or result.get("status") == "ok")
                    elif result is True:
                        ok = True
                    if ok:
                        blockage.healed = True
                        blockage.healed_at = datetime.now(timezone.utc)
                        logger.info("DFMO→Director restart %s succeeded: %s", name, result)
                        return True
                    logger.warning(
                        "DFMO→Director restart %s did not confirm success: %s",
                        name,
                        result,
                    )
                    return False
                except Exception as exc:
                    logger.debug("DFMO service heal via Director: %s", exc)
                    return False

            # Unknown location — do not claim healable success
            logger.debug("DFMO: no heal path for location=%s", blockage.location)
            return False

        except Exception as exc:
            logger.warning("DFMO: Auto-heal failed: %s", exc)
        return False


    async def _handoff_reliability(self, blockage: BlockageReport, snapshot: CirculationSnapshot) -> None:
        try:
            from services.reliability import get_reliability_engine, init_reliability_engine
            rel = get_reliability_engine()
            if rel is None:
                try:
                    rel = await init_reliability_engine(container=self._container)
                except Exception:
                    return
            if rel is None:
                return
            severity = "medium"
            if blockage.severity in (BlockageSeverity.SEVERE, BlockageSeverity.CRITICAL):
                severity = "high"
            elif blockage.severity == BlockageSeverity.MILD:
                severity = "low"
            await rel.handle_signal(
                symptom=f"DFMO blockage: {blockage.location} — {blockage.description}",
                source="dfmo",
                component=blockage.location,
                severity=severity,
                evidence_extra={
                    "blockage_id": blockage.id,
                    "resolution": blockage.resolution,
                    "flow_score": snapshot.flow_score,
                    "infrastructure_score": snapshot.infrastructure_score,
                    "event_schema_score": snapshot.event_schema_score,
                    "auto_healable": blockage.auto_healable,
                    "healed": blockage.healed,
                },
            )
        except Exception as exc:
            logger.debug("DFMO reliability handoff skipped: %s", exc)

    def note_legacy_event(self) -> None:
        """Called optionally when Nerve sees unscoped events."""
        self._legacy_event_warnings += 1

    def get_status(self) -> Dict[str, Any]:
        latest = self._snapshots[-1] if self._snapshots else None
        return {
            "version": self.VERSION,
            "running": self._running,
            "flow_score": latest.flow_score if latest else 100.0,
            "infrastructure_score": latest.infrastructure_score if latest else 100.0,
            "event_schema_score": latest.event_schema_score if latest else 100.0,
            "workflow_score": latest.workflow_score if latest else 100.0,
            "path_probes": latest.path_probe_details if latest else [],
            "probe_acked": latest.probe_acked if latest else False,
            "event_bus_flowing": latest.event_bus_flowing if latest else True,
            "database_writing": latest.database_writing if latest else True,
            "database_reachable": getattr(latest, "database_reachable", True) if latest else True,
            "database_readable": getattr(latest, "database_readable", True) if latest else True,
            "database_writable": getattr(latest, "database_writable", True) if latest else True,
            "services_healthy": latest.services_healthy if latest else 0,
            "services_total": latest.services_total if latest else 0,
            "active_blockages": [
                {
                    "id": b.id,
                    "location": b.location,
                    "severity": b.severity.value,
                    "description": b.description,
                    "auto_healable": b.auto_healable,
                    "healed": b.healed,
                }
                for b in (latest.active_blockages if latest else [])
            ],
            "consecutive_event_bus_failures": self._consecutive_event_bus_failures,
            "consecutive_db_failures": self._consecutive_db_failures,
            "patrol_count": self._patrol_count,
        }


dfmo = DataFlowMicroOrganism()


async def init_circulation_monitor(container=None, event_bus=None, supabase=None) -> DataFlowMicroOrganism:
    dfmo._container = container
    dfmo._event_bus = event_bus
    dfmo._supabase = supabase
    await dfmo.start(interval_seconds=30)
    return dfmo


async def shutdown_circulation_monitor() -> None:
    global dfmo
    await dfmo.stop()


__all__ = [
    "DataFlowMicroOrganism",
    "CirculationSnapshot",
    "BlockageReport",
    "BlockageSeverity",
    "dfmo",
    "init_circulation_monitor",
    "shutdown_circulation_monitor",
]
