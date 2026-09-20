# Phase F — Species scale — COMPLETE (code)

Goal: multi-process deploy, durable organisms, Chaos never in production.

## Checklist

| Item | Status | Location |
|------|--------|----------|
| SERVICE_MODE role catalog | Done | `core/species_scale.py` |
| Chaos forbidden in production | Done | `assert_chaos_allowed`, `ChaosMonkey.start`, settings validator |
| Durable `organism_state` table | Done | `migrations/015_organism_state.sql` |
| Hydrate census on boot | Done | `boot_species_scale` in lifecycle |
| Persist on organism create | Done | `OrganismRegistry.get_or_create` |
| `GET /api/v1/species/scale` | Done | organism_body_routes |
| Deploy notes | Done | `deploy/SERVICE_MODE.md` |
| Tests | Done | `tests/test_phase_f_scale.py` |

## Deploy sketch

| Process | SERVICE_MODE |
|---------|--------------|
| HTTP edge | `api` |
| Brain (Director/Loop/Synapses) | `core` |
| Schedulers / jobs | `workers` |
| Chaos drills | `chaos` + `ENVIRONMENT=staging` only |

## Operator

1. Apply `015_organism_state.sql`
2. Never set `SERVICE_MODE=chaos` with `ENVIRONMENT=production`
3. On graceful shutdown: `persist_all_organisms()`

## Full plan status

| Phase | Name | Status |
|-------|------|--------|
| A | Species law | Done |
| B | One nerve, one loop | Done |
| C | Organism per workspace | Done |
| D | Metabolism | Done |
| E | Body visible | Done |
| F | Species scale | Done |
