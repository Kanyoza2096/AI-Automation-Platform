# Phase E — Body visible — COMPLETE (code)

Goal: dashboard can poll real organism state (vitals / mind / hands / sleep).

## Endpoints (auth required)

| Path | Section |
|------|---------|
| `GET /api/v1/organism/vitals` | Pulse, heartbeats, bus, metabolism, synapses |
| `GET /api/v1/organism/mind` | Director proposals, council decisions, conditions |
| `GET /api/v1/organism/hands` | Publishes, job terminal, reflex firings |
| `GET /api/v1/organism/sleep` | Dreams + memory consolidations |
| `GET /api/v1/organism/body` | One-shot full snapshot |
| `GET /api/v1/organism/census` | Species-only: all living organisms |

Query: `?workspace_id=` (species operators only for other workspaces).

## Data sources

- `UnifiedEventBus` event store (filtered by workspace_id on nerve envelope)
- `OrganismRegistry.vitals()`
- `metabolism.status()`
- `SynapseMatrix.get_stats()`

## Files

- `api/organism_body_routes.py`
- `core/unified_bus.py` — `get_bus_sync` / `set_bus_instance`
- Mounted in `main.py` under `/api/v1`
- Tests: `tests/test_phase_e_body.py`

## Frontend (your part)

Point the dashboard cards at these four sections. No new UI was built here (backend only).

## Next: Phase F

Species scale — durable organism state, real `SERVICE_MODE` split, Chaos only in staging.
