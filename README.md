# Kanyoza Systems AI Platform

Multi-tenant **social content automation** backend with optional industry MIS plugins, Director/Meta/Condition engines, and Council governance.

**Stack:** Python 3.12 · FastAPI · Gunicorn/Uvicorn · Supabase · Gemini · Playwright (cards) · Socket.IO

> Historical docs (`KANYOZA_MASTER_CONTEXT.md`, old version stamps) may describe Flask or other stacks. **This README matches the current code.**

---

## What works as designed

| Area | Path |
|------|------|
| Content pipeline | `ContentGenerationPipeline.execute_async` — plan → research → LLM → validate → render → publish → analytics |
| Scheduling | `BrandScheduler` / `BrandSchedulerManager` + durable `AutomationJob` |
| Social plugins | Facebook (primary); Twitter/LinkedIn when tokens set |
| Engines | Director capabilities, Condition executor, Meta recovery handlers |
| Council | `council/` package — deliberation on publish and sensitive actions |
| Industry MIS | Church, Hospital, School, CRM, PDF merger under `plugins/industries/` |

---

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# Card rendering (optional):
playwright install chromium

export ENVIRONMENT=development
export GEMINI_KEY=your-key
export MASTER_API_TOKEN=your-secret
# Optional: SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY
# Optional: ENCRYPTION_KEY (Fernet), FACEBOOK_* tokens

uvicorn main:app --host 0.0.0.0 --port 10000
```

Health: `GET /health/live` · `GET /health/ready`

API docs: only when `ENVIRONMENT=development` (OpenAPI).

---

## Required vs optional env

| Variable | Required | Notes |
|----------|----------|--------|
| `GEMINI_KEY` | Production yes | Content generation |
| `MASTER_API_TOKEN` | Yes for admin routes | Fail-closed if empty |
| `SUPABASE_*` | For persistence | Jobs, workspaces, events |
| `ENCRYPTION_KEY` | Production | Fernet key for secrets |
| `FACEBOOK_*` / page tokens | For live publish | Dry-run pipeline works without |
| `SERVICE_MODE` | Default `full` | See table below |

See `ENV.md` and `config/settings.py` for the full list.

---

## SERVICE_MODE

| Mode | Role |
|------|------|
| `full` | API + engines + workers in one process (default local) |
| `api` | HTTP/API surface |
| `core` | Core services without full API extras |
| `director` / `meta` / `condition` | Split engine processes (`worker.py`) |
| `workers` / `monitor` / `github` | Background / ops |
| `chaos` | Chaos testing |

Cross-process messaging uses **SharedEventBus** (Supabase). In-process uses **UnifiedEventBus** (with legacy DomainEvent bridge).

---

## Project layout (important)

```
main.py                 # FastAPI app, lifespan, mounts
lifecycle.py            # Phased boot / shutdown / health
dependency_registry.py  # DI + engine capability binding
council/                # Democratic governance (canonical package)
services/pipeline/      # 10-stage content pipeline
scheduler/              # Brand / post schedulers
plugins/                # Social + industry + ops plugins
api/v1.py               # Large REST surface (split later)
products/               # Legacy — converter is Flask and NOT mounted
```

---

## Industry plugins

| Plugin | Domain models |
|--------|----------------|
| `church_mis` | Members, attendance, donations, events |
| `hospital` | Patients, visits, payments, clinic events |
| `school_mis` | Students, class attendance, fees, school events |
| `crm` | Contacts, deals, follow-ups |
| `pdf_merger` | PDF utilities |

Discovered at boot when `SERVICE_MODE` includes industry plugins (`full` / `api`).

---

## Security notes

- Do **not** commit tokens (`gh.txt` must stay empty of secrets).
- Admin routes require `MASTER_API_TOKEN` (no fail-open).
- Prefer authenticated `/api/v1/workspaces` — public tenant dump removed.
- Enable Supabase RLS in production (platform assumes you configure it).

---

## Phases completed in this tree

| Phase | Outcome |
|-------|---------|
| 0 | Boot honesty: Council, mounts, health, worker, auth fail-closed |
| 1 | UnifiedEventBus + legacy bridge; SharedEventBus = workers only |
| 2 | Async pipeline path, job mark published/failed, dry_run publish |
| 3 | Engines bind after boot; Director → scheduler/pipeline; Council imports fixed |
| 4 | Domain models for hospital/school; README; deprecate duplicate products |

---

## License / ownership

Internal Kanyoza Systems platform. Configure credentials for your own environments.

## Governed Action Execution Kernel

Business mutations now enter the typed `services/action_kernel.py` boundary.
The kernel validates `ActionIntent` against an `ActionContract`, enforces risk
and approval policy, records a durable `action_outbox` intent, and executes via
the existing `ExecutionLedger` and `ExternalActionExecutor` reliability plane.
The first governed family is CRM (`crm_add_contact`, `crm_add_deal`,
`crm_schedule_followup`, `crm_add_meeting`). See `docs/ACTION_EXECUTION_KERNEL.md`.
