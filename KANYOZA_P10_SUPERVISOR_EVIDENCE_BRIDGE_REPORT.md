# Kanyoza P10 — Cross-Supervisor Evidence Bridge

## Goal

Make it possible to compare Director's evidence-backed organism state with Meta Engine's latest per-engine health snapshot and the service registry, without merging different sources into one unsupported verdict or running new health probes from a reporting endpoint.

## Change

- Added `_supervisor_evidence()` in `routes/backend_contract_routes.py`.
- `/system-health` now includes a `supervisor_evidence` section with:
  - Meta Engine snapshot timestamp, overall status/scores, and per-engine status, timestamp, scores, failure class, root dependency, dependency list, and anomaly flags.
  - Director's existing `organism_state(max_age_seconds=300)`, retaining freshness, unknowns, issues, and source-reported condition.
  - Service registry topology and registration reports.
- The payload explicitly states that supervisor sources are reported separately and health signals are not merged.
- If a snapshot or supervisor is unavailable, the response says `unknown` with a reason; it does not synthesize an engine state.
- Added a regression test showing Meta Engine's `degraded` job-service evidence and Director's `unknown` observation remain distinct.

## Validation

- Compilation of changed route/test files: passed.
- Focused Director / topology / route / health-truth suites: **116 passed**.
- Full suite is not certified green because the current environment lacks the `supabase` package required by an existing publishing-pipeline contract test.

## Operational use

Use the new `/api/v1/system-health` `supervisor_evidence` section to compare sources and timestamps. If Meta Engine reports a dependency failure while Director reports unknown evidence, treat that as an evidence-coverage gap to investigate—not as consensus that the engine is healthy or critical.
