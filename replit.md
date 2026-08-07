# Kanyoza Systems AI Platform v11

## Project Overview

A multi-tenant social media AI automation platform that integrates with Facebook Messenger, Google Gemini, Twitter/X, LinkedIn, and optionally Supabase (persistence), Upstash Redis (caching), and GitHub (Code Guardian).

**Stack:** Python 3.12 · FastAPI (ASGI) · Gunicorn + UvicornWorker · python-socketio · Pydantic v2 · Supabase · Playwright

---

## How to Run

```
pip install -r requirements.txt && playwright install chromium
gunicorn main:app \
  --worker-class uvicorn.workers.UvicornWorker \
  --workers 1 --timeout 120 --bind 0.0.0.0:5000
```

The Replit workflow handles this automatically.

---

## Required Secrets

Set these in Replit Secrets before going live:

| Secret | Description |
|---|---|
| `GEMINI_KEY` | Google Gemini API key |
| `PAGE_ACCESS_TOKEN` | Facebook Page access token |
| `PAGE_ID` | Facebook Page ID |
| `VERIFY_TOKEN` | Facebook webhook verify token |
| `APP_SECRET` | Facebook app HMAC secret |
| `SESSION_SECRET` | Flask session secret (legacy, keep for compatibility) |

## Recommended Secrets

| Secret | Description |
|---|---|
| `GEMINI_MODEL` | Gemini model (default: `gemini-2.5-flash`) |
| `OWNER_PSID` | Messenger PSID for slash/bang owner commands |
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_ANON_KEY` | Supabase anon key |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase service role key |
| `UPSTASH_REDIS_REST_URL` | Upstash Redis URL (optional caching) |
| `UPSTASH_REDIS_REST_TOKEN` | Upstash Redis token |
| `MASTER_API_TOKEN` | Bearer token for dashboard/API access |

See `ENV.md` for the full list.

---

## Architecture

```
main.py              FastAPI entry point + lifespan startup
lifecycle.py         Service boot, scheduler start, graceful shutdown
dependency_registry  DI container wiring (all service bindings)
service_container    Lightweight IoC container (thread-safe)
config/settings      Pydantic-settings typed configuration
api/v1.py            All REST API routes (/api/v1/*)
routes/webhook.py    Facebook Messenger webhook handler
core/socket_server   python-socketio ASGI server (/dashboard namespace)
services/            Facebook, Gemini, ContentEngine, BrowserManager, etc.
scheduler/           Post, brand, and scan schedulers
plugins/             Facebook, Twitter, LinkedIn connector plugins
repositories/        Supabase-backed data repos (workspace, brand, etc.)
domain/              Analytics event recorder, security replay guard
guardian/            GitHub code quality scanner (optional)
```

---

## Production Fixes Applied (2026-07-29) — Spec Alignment

All changes follow the production fix specification. The existing architecture was preserved; only the identified gaps were corrected.

### api/v1.py
- **§1.1 WorkspaceCreateRequest** — added `slug`, `owner`, `plan` fields; `POST /workspaces` now auto-generates slug, enforces uniqueness
- **§1.2 PostCreateRequest** — added `brand_id`, `platforms`, `ai_generate`, `scheduled_for`, `workspace_id` fields
- **§1.3 Auto-brand removed** — `POST /posts` no longer auto-selects the first active brand; `brand_id` is required
- **§1.4 Scheduling** — `POST /posts` with `scheduled_for` creates a scheduled job record; `POST /posts` without it publishes immediately
- **§1.5 Job-centric** — `POST /posts` now returns `{"job_id": "...", "status": "queued"}`
- **§2 User management** — added `GET/POST/PUT/DELETE /users` with workspace isolation, duplicate-email protection, last-admin guard
- **§4.1/4.4 Secret redaction** — added `_strip_credentials()` helper that recursively removes secrets from `platform_config`, `credentials`, `config`, `settings`, `metadata`; all social account responses now use it
- **§4.2 Secret preservation** — `PUT /social-accounts/{id}` keeps existing credential when caller sends null/empty `access_token`
- **§6 Monitoring API** — added `GET /logs/stats` (totals/errors/warnings/success/rate) and `GET /logs/stream` (SSE real-time)
- **§8 Feature toggle persistence** — `POST /features/toggle` now upserts to `feature_flags` table in Supabase
- **§9 Rate-limit config** — added `PUT /rate-limits/config` with validation and Supabase persistence
- **§11.1 PDF executor** — added `POST /plugins/pdf_merger/execute` dedicated endpoint for Mutex
- **§13 Upload endpoint** — added `POST /upload` multipart file upload (PDF, 50 MB limit) → returns `file_id`; no more base64 JSON
- **§15 Webhook HMAC** — fixed `_verify_webhook_signature`: was `SHA256(body + secret)`, now correct `HMAC-SHA256(key=secret, msg=body)`
- **§16 CORS** — reads `MUTEX_URL` and `ALLOWED_ORIGINS` env vars; never falls back to `["*"]` in production
- **§17 Auth/container** — `ai/chat` passes the FastAPI service container to `CommandExecutor`

### services/command_executor.py
- **§10 Flask→FastAPI** — removed `from flask import current_app`; container is now injected via constructor parameter `container=`

### plugins/industries/pdf_merger/commands.py
- **§12 Smart PDF AI** — `_smart_merge` now sends the actual constructed prompt to `GeminiService.generate_raw()`; validates AI response schema (order indices, filename safety) before use; falls back to original order if AI fails or returns invalid output

### models/automation_job.py
- **§7 Workflow status** — `to_status_dict()` now returns canonical spec schema: `job_id`, `status` (queued/running/completed/failed/cancelled), `progress` (0-100), `current_step`, `started_at`, `updated_at`, `error`

### config/settings.py
- **§16 CORS settings** — added `mutex_url`, `allowed_origins` fields and `cors_allowed_origins` computed property

---

## Required Secrets (set in Replit Secrets)

| Secret | Description |
|---|---|
| `MUTEX_URL` | URL of your Mutex frontend (sets CORS allowed origin) |
| `ALLOWED_ORIGINS` | Additional comma-separated CORS origins |

---

## Bug Fixes Applied (2026-07-21)

1. **`core/persona.py`** — Added missing `Any` import → prevented NameError on `build_system_prompt()`
2. **`scheduler/scan_scheduler.py`** — Restored from `.Disabled` → fixes ImportError when Code Guardian is configured
3. **`core/render_queue.py`** — Fixed `AttributeError`: `created_at` stored as `datetime`, not string (`.isoformat()` called only in `to_dict`)
4. **`core/render_queue.py`** — Replaced Flask `current_app` with direct Redis init from settings (FastAPI-compatible)
5. **`core/render_queue.py`** — Fixed `RenderTask.from_dict()` to restore `retries` field (was always reset to 0)
6. **`services/news_scraper.py`** — Replaced Flask `current_app` with `get_plugin_registry()` → cross-platform publishing now works
7. **`core/workspace_context.py`** — Replaced Flask `request`/`g` in `init_workspace_middleware()` with FastAPI `BaseHTTPMiddleware`
8. **`lifecycle.py`** — Removed call to non-existent `_seed_audit_log_from_supabase` (seeding handled by lifespan in `main.py`)
9. **Gunicorn worker** — Changed `gthread` (sync) → `uvicorn.workers.UvicornWorker` (ASGI) for FastAPI compatibility
10. **`asyncio.get_event_loop()`** — Replaced all 8 occurrences across `api/v1.py`, `routes/webhook.py`, `lifecycle.py`, `workflows/base.py` with `get_running_loop()` (Python 3.10+ safe)
11. **Playwright** — Added `playwright install chromium` to startup command so browser is always available for card rendering

---

## User Preferences

- Fix bugs thoroughly and completely before marking done
- Production-ready quality is the goal
