# Phase 3 — Autonomous organism (2026-08-28)

## 16. DFMO write verification
**File:** `core/circulation_monitor.py`
- Split sensors: `database_reachable`, `database_readable`, `database_writable`
- `database_writing` is an honest alias of **writable only**
- Successful SELECT no longer implies write health
- `get_status()` exposes all three dimensions

## 17. Health semantics
**File:** `lifecycle.py` HealthRegistry
- Probe exceptions → `UNHEALTHY` with `confidence=low` (not “assuming healthy”)
- None/timeout remain DEGRADED with low confidence metadata

**File:** `core/circulation_monitor.py`
- `starting` / `degraded` removed from HEALTHY_STATUSES

## 18. Truthful auto-heal
**File:** `core/circulation_monitor.py`
- `event_bus`: concrete stop/start heal; marks healed only on success
- `services`: only marks healed if Director `restart_failed_service` confirms success
- Service blockages default `auto_healable=False` unless a real heal path succeeds
- Unknown locations never claim heal success

## 19. Probe ack bound
- `_probe_acks` hard-capped at 500 entries

## 20. Distributed lock on trend daily limit
**File:** `plugins/trend_intelligence/plugin.py`
- `_within_daily_limit` acquires `DistributedLock(trend:daily:{brand}:{date})`
- Combined with UNIQUE(trend_post_id, brand_id) from Phase 2 migration

## 21. Async-safe event publish
**Files:** `plugins/base.py`, `plugins/trend_intelligence/plugin.py`
- Detect coroutine from `bus.publish` and `create_task` / `asyncio.run`
- Prevents dropped UnifiedEventBus events

## Apply
```bash
tar xzf kanyoza-phase3-autonomous.tar.gz
```

## Related earlier phases
- Phase 1: publish path, Decision Gate, species_override, DI spine
- Phase 2: schema 019, trend→outbox, service-mode matrix, workspace filters
