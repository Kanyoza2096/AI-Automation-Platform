# Phase 53 — Production Certification Gate

## Objective

Convert production readiness from a collection of claims into explicit repository, deployment, and external-evidence gates.

## Implemented

- Mandatory runtime dependencies are represented in `requirements.txt`, including the database driver required by migration bootstrap tooling.
- Production API deployment uses Gunicorn with the Uvicorn worker and a live healthcheck.
- Security surface verification recognizes the stronger `require_species_privileges` dependency on control-plane routes.
- Migration verification uses deterministic numeric-prefix + filename ordering and reports duplicate/gapped historical prefixes as warnings rather than silently pretending the history is contiguous.
- `scripts/production_certification_gate.py` provides a static release gate.

## Evidence policy

`GATE: STATIC PASS` is **not** production certification. The following remain external staging gates:

1. Real Supabase/Postgres migration application from a clean database.
2. RLS and workspace-isolation tests against the real database.
3. Real Redis multi-process lease/claim/recovery tests.
4. Gunicorn HTTP smoke and readiness/liveness behavior under process restart.
5. Real OAuth/provider publication for every enabled channel.
6. Provider webhook/reconciliation tests with real provider IDs.
7. Crash injection between provider side effect and receipt persistence.
8. Throughput, latency, availability, and SLO measurements under representative load.

Unknown or unobservable external evidence must remain **blocked**, not healthy.
