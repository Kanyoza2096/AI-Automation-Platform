# Kanyoza Meta Engine and Condition Engine — Reality and Capability Audit

## Executive verdict

**Both engines are real implementations in the repository. Neither should be described as fully production-verified based on source inspection and local tests alone.** Their roles differ:

- **Meta Engine v7.1.0:** a real supervisory implementation with engine registration, bounded health probes, health snapshots, dependency graph, incident/diagnosis structures, learning/prediction structures, and governed recovery interfaces. Local tests confirm health-scan and registration contracts. Live runtime coverage and the actual health of external dependencies are not established by this audit.
- **Autonomous Condition Engine v5.1.0:** a real analytics/decision implementation. An isolated observation successfully generated a decision and baseline; the source implements anomaly/trend analysis, brand health, strategies, goals, experiments, decision outcome memory, and context learning. Its ability to mutate content is **adapter-dependent and narrower than the class's feature list**.

## Evidence collected

- `services/meta_engine/core.py`: `MetaEngineV7`, registration of live instances only, engine health probes, async monitoring lifecycle, snapshot-based health, dependency graph, diagnosis and recovery machinery.
- `services/condition_engine/core.py`: `AutonomousConditionEngine`, in-memory observation processing, baselines/trends/anomalies, decision generation, strategy/goal/experiment APIs, outcome recording, and optional persistence.
- `services/condition_engine/api.py`: singleton `autonomous_engine`; its default executor explicitly returns `action_executor_unavailable` and performs no action.
- `lifecycle.py`: boots Condition Engine, wires the event bus, optionally binds Supabase, and attempts Council-governed executor wiring; on wiring failure it restores the fail-closed default executor. Meta Engine is registered against the final service map and its monitoring loop is started after boot registration.
- `council/integration/condition_engine_integration.py`: only a limited set of content mutations have a concrete execution branch. Other proposed actions return `no_executor_for_action:<action>` rather than being falsely reported as executed.
- Existing tests: `tests/test_engine_truth.py` and `tests/test_phase19_meta_engine_signals.py` — **35 passed** together in this audit run.
- Reproducible smoke audit: `python scripts/audit_meta_condition_engines.py`.

## Capability classification

| Capability | Classification | Evidence / limit |
|---|---|---|
| Condition observation → baseline/trend/anomaly/decision | Implemented locally | Isolated in-memory observation generated a decision payload. Real production data feed is not proven. |
| Condition strategy/goal/experiment/learning APIs | Implemented in source | Local methods and data structures exist; sustained quality of learning requires real outcomes and persistent data. |
| Condition decision persistence | Optional / dependency-bound | Uses Supabase only when a client is injected and persistence is enabled; persistence failures are logged and do not fail the decision call. |
| Condition action execution | Partial / adapter-bound | API fallback is intentionally fail-closed. Lifecycle attempts Council-governed wiring. Only several named content actions have concrete branches; unsupported actions fail closed. |
| Condition health readiness | **Reporting gap** | `check_health()` returns `healthy` based on local counters even if `action_executor` is absent or is the fail-closed fallback. API `health_check()` also hardcodes `healthy`. This does not mean execution is ready. |
| Meta registration and topology | Implemented locally | Live instance registration, alias normalization, and topology reporting exist; topology presence is not health. |
| Meta health supervision | Implemented locally | Bounded probes, health snapshots, explicit unknown status, and dependency-aware classification are present. This audit did not start the real monitor or query live services. |
| Meta recovery | Implemented but gated | Recovery handlers are registered only for instances exposing restart/reset; other paths can report unsupported recovery. Recovery safety/efficacy against real services is unverified. |
| Meta predictive/learning features | Implemented structures and methods | Actual predictive usefulness depends on sufficient real samples and durable state; not certified by this smoke test. |
| Supabase, Redis, cross-service events, external adapters | Not verified | No live credentials or production connections were used. |

## Important interpretation

A `healthy` result from Condition Engine currently means the local object answered its health check; it is **not an execution-readiness certificate**. In the API singleton, the default executor is designed to return a failure. Lifecycle code tries to replace it with a Council-governed executor, but whether that path succeeded in a running deployment must be checked in the runtime.

Likewise, Meta Engine can report a registered engine as nominal when the engine's own health method returns `healthy`. Since Condition Engine's health method does not expose executor readiness, Meta's current signal can inherit that blind spot. Meta's topology and health reports must not be read as proof that every capability has a working external action adapter.

## Priority fixes recommended

1. Add separate `analysis_ready`, `execution_ready`, and `persistence_ready` fields to Condition Engine health output. Report executor binding as unavailable when `None` or the fail-closed default; do not conflate this with analysis health.
2. Make the API-level `health_check()` delegate to `autonomous_engine.check_health()` instead of hardcoding healthy.
3. Add a startup assertion/diagnostic that confirms Council-governed executor wiring was installed; if not, expose a degraded execution-readiness field while keeping actions fail-closed.
4. Add contract tests for each supported Condition action and for unsupported actions. Verify both the canonical `ok` field and authoritative postcondition, not just handler return shape.
5. In Meta Engine, report per-engine analysis/operation readiness separately from process liveness and dependency status.
6. In isolated staging, verify that the Meta monitor actually starts, expected services are registered as live instances, Condition Engine receives real observations, and any approved mutation has durable ledger receipts and observable outcomes.

## Local validation

- Existing Meta/engine-truth plus P19 regression selection: **35 passed**.
- Reproducible in-process audit script checks source contracts and one isolated observation/health scan without starting the Meta monitor, executing actions, or contacting external services.
- Production readiness: **NOT VERIFIED**.
