# Phase F / Sprint 5 — Multi-process deploy

## Modes

| SERVICE_MODE | Role |
|--------------|------|
| `full` | Everything in one process (dev) |
| `api` | HTTP edge, body/timeline/slo routes |
| `core` | Director, Meta, Condition, Autonomous loop, SLO alerter |
| `workers` | BrandSchedulers, TaskQueue, publish outbox drainer |
| `chaos` | Staging drills only — blocked in production |

## Run

```bash
# Single process (dev)
SERVICE_MODE=full uvicorn main:app --reload

# Split (prod)
SERVICE_MODE=api ENVIRONMENT=production uvicorn main:app --host 0.0.0.0 --port 8000
SERVICE_MODE=core ENVIRONMENT=production python -m workers.brain
SERVICE_MODE=workers ENVIRONMENT=production python -m workers.scheduler_worker
```

Or: `docker compose -f deploy/docker-compose.production.yml up -d`

## Shared state

- Same Supabase (jobs, outbox, organism_state, plans)
- Same secrets (`MASTER_API_TOKEN`, `ENCRYPTION_KEY`, social tokens)
- Optional Redis for cross-process event fan-out

## SLO alerts

```bash
export SLO_ALERT_WEBHOOK_URL=https://hooks.slack.com/services/...
export SLO_ALERT_COOLDOWN_SECONDS=300
export SLO_ALERT_ENABLED=true
```

## Weekly report

```bash
curl "$HOST/api/v1/reports/workspace/$WS?window_days=7" \
  -H "Authorization: Bearer $TOKEN"
```

On shutdown: `persist_all_organisms()` (species_scale).
