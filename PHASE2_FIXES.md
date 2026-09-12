# Phase 2 fixes (2026-08-28)

## 1. Schema — `migrations/019_missing_core_tables.sql`
Creates tables referenced by app code but missing from earlier migrations, including:
- events, event_store, shared_event_bus, dead_letter_queue
- post_history, posts, post_engagement, scheduled_posts, scheduler_state
- rate_limit_config (code name; 012 only had rate_limits)
- monitor_targets, monitor_checks (DFMO)
- trend_* (sources, posts, decisions, brand_rules, publish_jobs, page_posts)
- director_* , meta_engine_snapshots, autonomous_*
- council_*, backup_*, conversation_*, growthbridge_*, subscriptions, tenants, …

Run on Supabase after prior migrations 001–018.

## 2. Trend → outbox bridge — `services/trend_publish_bridge.py`
- Removed non-existent `DirectorService` import
- Issues DecisionGate id explicitly
- Enqueues **PublishOutbox** (durable path)
- Optionally notifies `Director` capability if bound
- `wire_trend_publish_bridge(bus)` subscribes to `trend.publish.requested`
- Boot step **TrendPublishBridge** in lifecycle for modes full/api/workers/core

## 3. Service-mode boot matrix — `main.py`
Bio-systems (Heartbeat, Reflex, Dreams, DFMO, Universal Pulse, Reliability) only start when:
`SERVICE_MODE` ∈ {full, core, director, meta, condition, monitor, chaos}

Pure **`api`** skips organism patrols → no multi-instance DFMO duplication.

## 4. Workspace isolation
- `api/v1/analytics.py` — post_history queries filtered by `workspace_id`
- `api/v1/knowledge.py` — list/create attach and filter `workspace_id`

## Apply
```bash
tar xzf kanyoza-phase2-fixes.tar.gz
# Then run SQL:
# migrations/019_missing_core_tables.sql  in Supabase
```

## Env
```bash
SERVICE_MODE=full   # or api for API-only process
```
