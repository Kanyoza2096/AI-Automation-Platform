# Fixes Applied (2026-08-26)

## Health noise / Director false degraded spam
- **core/autonomous_loop.py** (v3.2): Debounced Meta health alerts (transition + 5 min cooldown). Rich alert detail. Platform workspace_id on infra events. Accurate META/LOOP log attribution.
- **services/meta_engine.py**: STALE engines no longer flip overall organism status. Starting (no snapshot) is not "degraded". Clear human-readable health messages. Platform-scoped publishes.

## Nerve / event scope
- **core/nerve.py**: Species/platform allow-list includes heartbeat.system, heartbeat.beat, service_heartbeat, engine_alert, engine_status_update, system.*
- **core/heartbeat.py**: Publishes with workspace_id=\"platform\".

## Syntax
- **api/v1/ai_runtime.py**: Fixed IndentationError on nested try (metabolism consume).

See conversation analysis for full diagnosis.

## Director capabilities (functional completeness)

- **Root cause:** `lifecycle.py` imports `services.director_bindings` which **did not exist**.
  Import failed → all capabilities stayed as `delegated` placeholders.
- **Fix:** Added `services/director_bindings.py` binding all **non-critical** capabilities
  to real services (scheduler, content_engine, condition_engine, health_registry,
  meta recovery, guardian, growth, reporting).
- **CRITICAL** capabilities remain intentionally blocked (human approval).
- Expected boot log: `Director hands bound: 15 capabilities` (approx).

## Dependencies

- `requirements.txt`: added `Pillow>=10.0` (rendering/cards).
- Optional playwright / google-api-python-client documented as comments.

## Flask residual

- `products/converter/routes.py` and `products/shared/auth.py` marked LEGACY;
  not registered on FastAPI (dead code until migrated).


## Async capability execution (Director)

- **Bug:** `_execute_async` always used `run_in_executor(None, cap.handler, params)`.
  Async handlers from `director_bindings` were never awaited (coroutine object stored as result, or runtime errors).
- **Fix:** `_invoke_capability_handler`:
  - `async def` handlers → `await handler(params)` on the event loop
  - sync handlers → thread-pool executor (non-blocking)
  - if sync returns awaitable → await it
- `bind_capability` logs `async` vs `sync` for observability

## Content / image generation (2026-08-26)

- **Missing module:** `services/cloudflare_image.py` was imported by API but absent — created with FLUX.1-schnell + SDXL free models.
- **image_engine v4:** Chain is now Cloudflare → HF FLUX → Kroki (comparisons) → Picsum (was Kroki/Picsum only = stock quality).
- **HF FLUX:** Tries `router.huggingface.co` then legacy `api-inference.huggingface.co`.
- **AIResponse:** Compatible with Groq/Mistral `error=` field; providers no longer pass invalid `success=` kwarg.

## Production log fixes (2026-08-26 evening)

1. **universal_pulse** — TABLE_TIME_COLUMNS map (trend_posts→fetched_at, monitor_checks→checked_at, director_observations→timestamp, backup_records→started_at). Stops PostgREST 400. Stagnation alerts try kanyoza_notifications then email_service. Platform workspace_id on universal.stagnation.
2. **health_monitor_service** — await async health_registry.check_all(); safer sync wrapper via run_coroutine_threadsafe.
3. **condition_engine** — accept event_bus=/**kwargs; set_event_bus(); dependency_registry no longer passes invalid kwarg.
4. **trend_intelligence** — warn when NEWSAPI_KEY / TWITTER_BEARER_TOKEN missing; support platform aliases.
5. **nerve** — universal.* as species/platform prefix.


## Reliability Engine (Self-Healing subsystem)

Added `services/reliability/` — first-class governed remediation:

- **Pulse** — collect circulation, meta, health registry evidence
- **Diagnostician** — rule-based cause + confidence (learns from memory)
- **Repair Planner** — typed playbook actions only
- **Policy Guardian** — autonomy levels 0–5; no auto code deploy
- **Executor** — restart/refresh/requeue/reconcile/escalate
- **Memory** — records outcomes for similar-incident matching

Wired into Autonomous Loop on `engine_alert` and boot in `main.py`.
Env: `RELIABILITY_AUTONOMY_LEVEL` (default `2_reversible`).


## Trend Intelligence v2.0

- Staged events: `trend.detected`, `trend.scored`, `trend.decision.created`,
  `trend.publish.requested`, `trend.duplicate`, scan telemetry — **no double trend_detected**.
- Workspace_id on all event envelopes.
- Durable dedup via `trend_posts.url` (+ soft process cache).
- Async loop preferred over bare Thread; interruptible thread fallback.
- **Decoupled publish**: no `scheduler.trigger_manual()` / Facebook from plugin;
  emits `trend.publish.requested` + optional `trend_publish_jobs` row.
- Cross-workspace rule/trend skip when both have workspace_id.
- Structured error logging (no silent bare except on core paths).


## Meta / Director boot + offline grace (2026-08-27)

- **Root cause of MetaEngine degraded at boot:** `await init_meta_engine(...)` awaited a **sync** function's return value (MetaEngine instance) → TypeError. Fixed in lifecycle.
- Director boot: soft-fail discover/start/health register so step completes.
- Meta health: missing/failed ping engines are **STALE/DEGRADED** for first cycles; OFFLINE only after 3 consecutive failures and cycle_count >= 2.
- Director: `execute_capability()` public API for trend publish bridge / Reliability.


## Trend publish handoff + semantic score (2026-08-27)

- `publish_scheduled_content` posts **explicit trend content** via Facebook when
  `source=trend_intelligence` (or job payload with content), instead of generic scheduler content.
- Records `trend_page_posts` / updates `trend_publish_jobs` status when possible.
- Optional `TREND_SEMANTIC_SCORE=true` blends Gemini relevance into score (30%).
- Reliability engine lazy-inits on first engine_alert if boot missed it.


## Trend multi-tenant + LLM briefing (v2.3)

- SQL `migrations/002_trend_multitenant.sql`: workspace_id columns, unique indexes, RLS policies, publish jobs table.
- process_trends soft-filters by `DEFAULT_WORKSPACE_ID` when not `platform`.
- Decisions persist `workspace_id`.
- Optional `TREND_LLM_BRIEFING=true` rewrites post body via Gemini before `trend.publish.requested`.


## DFMO v1.3 — real immune loop start

- Patrol calls **auto_heal** for mild/moderate healable blockages (event_bus restart, soft meta cache clear).
- **Active bus probe** `circulation.probe` + in-process ack (not lifetime counters alone).
- **Retention**: purge `monitor_checks` older than 24h (prefers `source=dfmo_probe`).
- Blockages → **Reliability Engine** `handle_signal`.
- Split scores logged: `infra=` / `schema=` / `flow=` (+ `probe_ack=`).

## DFMO v1.4 — end-to-end path probes

- core/circulation_path_probes.py: db_read, publish_dry_run, facebook health, content path
- DFMO runs path probes every 3rd patrol; workflow_score blends into flow
- Critical path failures become path:* blockages



## Web Intelligence v1.0

- `services/web_intelligence/`: gateway + providers (Tavily, Brave, NewsAPI, DuckDuckGo, Reddit)
- Director capabilities: `web_search`, `web_fetch`
- Boot: WebIntelligence step
- API: `GET /api/web/search?q=...`, `/api/web/providers`
- Trend: optional `TREND_WEB_VERIFY=true` cross-checks titles via search before decision


## Tool-using agent (LLM decides tools)

- `services/tool_agent.py`: bounded ReAct/JSON loop (web_search, web_fetch)
- API: `POST /api/web/ask` body `{"question": "..."}`
- Director capability: `ask_with_tools`


## Messenger → tool agent routing

- `services/chat_routing.py`: heuristics for live-knowledge questions
- `routes/webhook.py`: tries tool agent before Gemini for those messages
- Env `CHAT_TOOL_AGENT` (default true), `CHAT_TOOL_AGENT_IN_GEMINI` optional


## Gap closure batch

- Unified health model + GET /api/health/unified
- DFMO logs unified health every 5 patrols
- DFMO service heal → Director restart_failed_service
- restart_failed_service: Meta recovery + container stop/start
- Trend DIAG when zero activity (missing keys/sources)
