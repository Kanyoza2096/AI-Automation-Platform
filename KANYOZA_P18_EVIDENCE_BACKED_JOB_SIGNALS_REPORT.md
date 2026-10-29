# Kanyoza P18 — Evidence-backed Director job-backlog advisory

## Objective
Move the signal publisher beyond an always-empty interface without inventing operational conditions or bypassing Director governance.

## Change
`JobService.health_check()` now derives one conservative advisory from explicit `JobStatsService` evidence only when all of the following hold:
- the source reports a healthy/nominal/ok/ready status;
- `stats.status_counts` is an actual mapping;
- `FAILED` and `STALE` counts are parseable, nonnegative counts;
- at least one failed or stale job exists.

The emitted recommendation uses the existing `optimize_infrastructure` capability and its `component=job_service` target. It is an advisory review only: it does not retry jobs, mutate records, or execute a recovery action. Degraded/unhealthy/unknown results, missing stats, malformed counts, and zero counts produce no signal. Existing Director freshness, source-health, sector, capability, and governance checks remain in force.

## Tests and validation
- Added `tests/test_phase18_evidence_backed_job_signals.py` for evidence-backed publication, suppression on degraded/unknown status, missing/malformed evidence, and end-to-end Director capability/sector acceptance.
- Python compilation passed.
- Focused combined regression suite: **136 passed**.
- ZIP integrity verified after packaging.

## Scope and limitations
- This phase adds an evidence-backed publisher for the job backlog. It does not fabricate signals for `meta_engine` or `autonomous_content`; their current publishers remain explicitly empty until their own diagnostics justify a safe recommendation.
- Tests use repository-level fixtures; production observer registration, live Supabase metrics, and deployment behavior were not exercised here.
- The full repository test suite is not claimed as passing.
