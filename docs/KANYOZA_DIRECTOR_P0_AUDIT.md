# Kanyoza Director — Master Plan Implementation Update

**Scope:** source-level patch against the provided Kanyoza repository archive, aligned to the user's Master Intelligence & Autonomy Engineering Plan. This is not evidence of a live deployment.

## Mission alignment

The primary objective is to upgrade Director as KDO's supervisory intelligence and autonomy engine. This pass advances trustworthy organism observation and evidence-based outcome verification without adding new engines for their own sake.

## Changes in this pass

3. **Dependency-aware mission graph** (`services/director/core.py`, `services/director/models.py`)
   - Tasks now support optional `mission_id`, `depends_on`, `success_criteria`, and `recovery_policy` metadata without changing legacy defaults.
   - `add_task_dependency()` persists dependency metadata through the existing task store, rejects self-links, missing tasks, cycles, execution-started tasks, and cross-mission edges.
   - `_execute_due_async()` waits until every prerequisite is `completed` or `verified`; missing or terminal-unsuccessful prerequisites cause the dependent task to be marked `blocked`.
   - Authenticated `GET /missions/graph` returns the current task graph, and `POST /tasks/{task_id}/dependencies/{dependency_id}` adds a scoped dependency edge.
   - Startup now restores persisted task records and reconstructs dependency edges from the existing task store. Tasks found in `EXECUTING` at restart are moved to `FLAGGED` for reconciliation rather than blindly retried, because an external side effect may already have occurred.
   - This is a dependency-aware task graph, not yet a full mission planner: deadlines, compensation, and automatic replanning remain open. A conservative structured success-criteria evaluator is now implemented; free-text criteria remain non-machine-evaluable.

1. **Authenticated organism-state endpoint** (`GET /organism/state` and `GET /decision-context`)
   - Exposes the Director's actual in-memory observations with observer name, source, sector, source-reported status, metrics, issues, message, and observation timestamp.
   - Reports freshness per observation (`fresh`, `stale`, or `invalid_timestamp`) and an aggregate evidence condition without synthesizing missing engines or values.
   - Supports a bounded `max_age_seconds` query parameter (1–86400); invalid values return HTTP 400.
   - This is a source-level evidence view, not yet a typed source-authority contract or a durable historical World Model.

2. **Observation provenance retained** (`services/director/core.py::_snapshot_observations`)
   - Execution snapshots preserve `source` and `observed_at` in addition to status, metrics, and issues.
   - This provides a basic trace to the originating observer; source authority is not yet typed.

2. **Fresh, authoritative domain evidence required** (`_authoritative_resource_state`)
   - Only accepted observer statuses (`healthy`, `nominal`, `ok`, `ready`) are considered.
   - Observation timestamps must parse and be no more than five minutes old (with a small future-clock tolerance).
   - Resource data must appear in the explicit observer metrics contract; handler acknowledgements and intent logs are not substituted for observations.

3. **Posting schedule verification is strict** (`_verify_schedule_change`)
   - Requires `metrics.brands[brand_id]`, matching `workspace_id`, and `posting_schedule.content_hours` equal to requested hours.
   - Missing, stale, unhealthy/unknown, malformed, or cross-workspace evidence returns false.

4. **Worker pool verification is strict** (`_verify_worker_scale`)
   - Requires `metrics.worker_pools[pool_name]`, matching `workspace_id`, and an observed integer `worker_count` exactly matching `target_workers`.
   - A logged intent, queue-depth change, or missing observation is not proof of scaling.

5. **Regression coverage added** (`tests/test_director_outcome_verification.py`)
   - Covers successful matching observations, stale/unknown observations, mismatched values, missing evidence, invalid targets, and workspace mismatches.

## Verification

- **89 tests passed** in the latest combined run covering all `tests/test_director*.py` files plus engine/health truth, execution ledger, Action Kernel, and business handoff tests.
- `python -m py_compile services/director/core.py services/director/api.py services/director/models.py tests/test_director_mission_graph.py` passed.
- The ZIP archive is integrity-checked after packaging.
- The full repository suite, live deployment, production Supabase persistence, and real external side effects have **not** been verified.

## Important limitations

- The existing observer registry does not yet establish that real observers populate `metrics.brands` and `metrics.worker_pools`. Until that integration exists, the strict predicates correctly leave those outcomes unverified.
- `scale_worker_pool` currently records intent; it does not provision or resize workers.
- `_build_plan_async()` attaches structured decision context. The new dependency graph is currently in-memory plus existing per-task persistence; startup reconstruction, deadlines, compensation, automated replanning, and mission lineage are not implemented, and `decide()` remains primarily rule-based.
- Engine discovery and observer registration do not prove that specialized engine reasoning is consumed by Director.
- This pass is not production certification and does not claim live task execution.

## Next steps from the master plan

### P1 — Intelligence foundation
- Extend the evidence contract with explicit source authority and conflict-resolution policies.
- Connect actual telemetry, events, incidents, topology, World Model, and memory through supported interfaces.
- Expand the traceable decision context to include validated assumptions and specialized-engine evidence where supported.

### P2 — Decision-making and mission planning
- Integrate specialized engine outputs where real contracts justify it.
- Build durable mission and step state with dependencies, prerequisites, success criteria, cancellation, recovery, and explainable replanning.

### P3 — Governed autonomy
- Wire authoritative domain-state observers for schedule and worker pool.
- Verify canonical Action Kernel/ledger/receipt/recovery behavior in staging, including retries and uncertain outcomes.

### P4 — Production readiness
- Run the full test suite and end-to-end security, tenancy, persistence, and recovery tests.
- Define measurable acceptance thresholds and verify live operational evidence.

## Release status

**Not production-certified.** Targeted tests support the source changes described above; they do not establish live runtime readiness or successful real-world task execution.

## Latest pass — engine-to-decision signal contract

Implemented an opt-in, structured engine recommendation contract at `metrics.director_signals` (contract identifier `metrics.director_signals/v1`). Director accepts recommendations only from fresh observations, requires a registered and enabled capability, enforces observer-sector/capability-sector agreement, validates description/params/confidence shape, and reports rejected signals with reasons. Accepted signals are included in `/decision-context` and can produce advisory tasks through the existing task/policy path. Engine-originated recommendations cannot silently authorize a capability whose authority is `AUTO`; those tasks are moved to approval-required status. This is a contract and integration path, not a claim that every production engine currently emits recommendations.

Latest focused regression run: **93 passed** across Director, engine-health truth, execution ledger, Action Kernel handoff/replay, and business-handoff test groups. Python compilation passed. This is not a full-repository test run or live-deployment verification.

## Latest pass — value-aware planning metadata

Engine recommendations may now include optional `impact_score` and `mission_priority` values in the range 0–100, plus up to ten short `success_criteria` strings. Director validates these fields, rejects malformed values, carries accepted metadata onto the created task, exposes the fields in the mission graph, and includes bounded impact/mission-priority weights in task ranking. These fields are advisory only: they do not change capability authority, bypass approval, or prove that a success criterion has been achieved. Success criteria remain declarative metadata until an authoritative outcome evaluator is wired.

Latest focused regression run: **96 passed** across Director, engine-health truth, execution ledger, Action Kernel handoff/replay, and business-handoff tests. Python compilation passed for the updated model, core, and engine-signal tests. Full repository and live deployment remain unverified.


## Latest addition: evidence-backed success-criteria reconciliation

- Tasks may retain either descriptive string criteria or structured criteria shaped as `{observer, metric_path, operator, expected}`. Supported operators are `equals`, `not_equals`, `gt`, `gte`, `lt`, `lte`, `contains`, and `exists`.
- `POST /tasks/{task_id}/verify-outcome` is authenticated, write-authorized, workspace-scoped, and supports a bounded `max_age_seconds` window.
- The evaluator reads only the explicitly named observation, requires a fresh timestamp and an accepted healthy status, and distinguishes `verified`, `ineffective`, and `unknown`. Missing, stale, unhealthy, type-incompatible, or free-text-only evidence remains `unknown`; it is not treated as proof of failure or success.
- Only tasks already in a completed/outcome state are reconciled. The resulting verified/ineffective state and evidence summary are persisted through the configured task store; unknown evidence does not rewrite task outcome state.
- Structured criteria are validated when accepted from `metrics.director_signals`. The evaluator does not infer semantics from natural-language criteria.
- Latest focused combined regression run: **106 passed** across Director, engine-health truth, execution ledger, Action Kernel handoff/replay, and business-handoff tests. Python compilation passed. Full repository suite and live deployment remain unverified.


## Latest addition — evidence-guided recovery assessment

- Added `Director.assess_task_recovery(task_id)` and authenticated read-only `GET /tasks/{task_id}/recovery-assessment`.
- The assessment classifies interrupted (`FLAGGED`), failed, ineffective, blocked, in-progress, cancelled/skipped, and healthy terminal tasks; reports prerequisite states; and suggests bounded next steps.
- It explicitly returns `automatic_action_taken: false` and `automatic_retry_allowed: false`. Failed or interrupted tasks require evidence reconciliation and normal authorization before any retry, avoiding duplicate external side effects.
- The API checks task visibility and refuses to disclose a dependency from another trusted workspace. It performs no execution or state mutation.
- Latest focused combined regression run: **111 passed** across Director, engine-health truth, execution ledger, Action Kernel handoff/replay, and business-handoff test groups. Python compilation passed. Full repository tests and live deployment remain unverified.

## Master-plan phase accounting (as of this source-level pass)

The supplied plan defines **19 phases (Phase 0 through Phase 18)**. Current changes span parts of Phases 0, 1, 2, 3, 4, and 6, but feature delivery is not equivalent to satisfying every phase's acceptance criteria. Phase 0 has a source audit and substantial P0 hardening, but the full baseline, complete security review, and production verification are not certified complete. Accordingly, **0 phases are formally marked complete and 19 remain open under strict acceptance gating**. Practically, Phase 0 is the active foundation/audit stage; after it is formally closed, 18 numbered phases remain. This is a conservative status count, not a claim that no progress has been made.


## Batch continuation — evidence gating and suite triage (2026-10-20)

### Director decision hardening

- Engine recommendations in `metrics.director_signals` are now accepted only when the source observation is fresh and reports an accepted healthy status (`healthy`, `nominal`, `ok`, or `ready`). Fresh observations reporting unhealthy/unknown states produce explicit rejection reasons rather than recommendations.
- If fresh observers within the same sector report conflicting statuses, Director suppresses engine recommendations from that sector until evidence is consistent. This is deliberately conservative; the conflict remains visible in decision context.
- Rule-based issue triage now ignores stale or malformed-timestamp observations when creating new remediation tasks. Fresh degraded/critical observations remain eligible to trigger issue triage; stale observations are not treated as current.
- `decision_context.evidence_policy` explicitly documents these gates.
- Added regression tests for unhealthy-source rejection, conflicting-sector suppression, and stale issue suppression.

### Test-suite triage

- Corrected two test imports that referenced a non-existent `Backend.services` package; the repository modules are under `services`.
- Corrected a chaos-boundary test to call the actual webhook signature helper in `api.v1_stores`, where it is implemented. This does not weaken webhook verification.
- Focused combined regression suite: **114 passed**. Python compilation passed for updated Director code and the touched tests.
- Full-suite run reached **655 passed** before stopping at `tests/pipeline/test_publish_end_to_end_contract.py::test_connector_receives_brand_for_tenant_specific_facebook_credentials`. The test expects a publish success without a resolvable Supabase client, while the current canonical pipeline deliberately fails closed when its durable execution ledger cannot be initialized. The production fail-closed behavior was not weakened to make this environment-dependent test pass.
- A full suite excluding that pipeline test did not finish within the available run window, so the full repository suite remains **not certified passing**. The `supabase` Python package/client was unavailable in this runtime.

### Phase accounting

The master plan remains 19 numbered phases (0–18). This batch advances portions of the intelligence foundation and decision-planning phases, but does not close any phase formally because phase-wide acceptance criteria, real engine telemetry, and production persistence remain unverified. **Strict status: 0 formally closed; 19 open.**

## Latest batch: diagnosis evidence gate (2026-10-20)

The Director root-cause path now applies a dedicated evidence gate before invoking `RootCauseAnalyzer` and before it can create root-cause remediation tasks. It accepts only observations with a valid timestamp inside a 300-second freshness window, known source status, and a sector status that does not conflict with another fresh observer in that sector. Future-dated, stale, malformed, and `UNKNOWN` observations are rejected; all observations in a conflicting sector are excluded from automated diagnosis. The raw observation set remains available to reporting. The gate decision is recorded in `last_diagnosis_evidence` for local diagnostic inspection.

New tests in `tests/test_director_diagnosis_evidence_gate.py` cover stale critical observations, unknown status, same-sector conflicts, and fresh consistent critical evidence. Validation: `119 passed` across the focused Director, engine-health, execution-ledger, Action Kernel, and business-handoff regression command; Python compilation passed. This is not a full-repository pass and does not certify live deployment or real telemetry wiring.

The authenticated `/diagnostics` response now includes the last diagnosis evidence-gate summary (accepted observers, rejection reasons, and conflicting sectors). Added an API regression test for this field. The refreshed focused test run passed **119 tests**.

## Latest batch: execution idempotency and persistence gate (2026-10-20)

- `_execute_async()` now permits entry only from explicitly runnable task states (`planned`, `approved`, `approved_pending_execution`). Duplicate requests for executing or terminal tasks return without re-running a handler or governed handoff.
- Before either legacy side-effecting handlers or proposal-backed canonical handoffs are invoked, Director persists the `EXECUTING` state. A store that explicitly reports a failed write causes execution to be deferred and the task flagged; the side effect is not started.
- `SupabaseDirectorStore.save_task()` and the in-memory store now return a persistence acknowledgement, allowing the execution path to fail closed when the storage adapter reports failure. This is not a claim that a remote database was available or that the in-flight write is transactionally durable in production.
- Added five regression tests for completed/approval-required task replay, concurrent duplicate execution, and persistence failure before legacy and governed execution.
- Validation: the idempotency tests passed **5/5**; a further focused Director/engine-health/ledger/Action Kernel/business-handoff batch passed **93/93**. Total across these two targeted invocations: **98 passed**. Python compilation passed. Full repository and production integration remain uncertified.

## 2026-10-20 batch: engine signal coverage observability

### Finding
The Director implements validation for `metrics.director_signals/v1`, but a repository-wide source scan found no production Python publisher of the `director_signals` field outside the Director's own consumer and tests. Engine observers therefore can be discovered and monitored without actually supplying actionable recommendation signals. This is an integration-coverage gap, not proof that no external/runtime publisher exists.

### Change
`Director._engine_decision_signals()` now returns per-observer `coverage` and a `coverage_summary`, including observers that do not publish a correctly shaped signal list. This makes absent integration explicit rather than confusing an empty accepted-signal list with proof that engines have nothing to recommend. This is diagnostic only and grants no additional execution authority.

### Validation
- Python compilation: passed for changed Director code and test.
- Focused engine-signal, evidence-gate, idempotency, organism-state, and mission-graph tests: **36 passed**.
- Broader Director/engine-truth/connector-health/execution-ledger/action-kernel regression selection: **125 passed in 2.43s**.
- Production engine signal publishers remain unverified/not found in the scanned Python source. Next step is to identify the real runtime engine instances and add explicit, tested publishers at the appropriate engine boundary, rather than fabricate synthetic recommendations in Director.

## Latest batch — observer truth and discovery-side-effect boundary (2026-10-20)

- Corrected `services/director/core.py::_make_observer`: `get_status` and `health_check` results without an explicit `status` or `overall` now remain `unknown`; `get_stats` alone is not health evidence.
- Restricted factory invocation during engine discovery to the explicit `ENGINE_FACTORY_GETTERS` allowlist for `get_job_stats_service` and `get_render_queue`. Arbitrary registered callable exports are not executed during discovery, avoiding accidental request-handler execution and startup side effects.
- Added `tests/test_director_observer_contracts.py` covering absent status fields, stats-only payloads, preservation of reported degraded status, and safe callable discovery.
- Focused combined regression suite: **114 passed** across Director tests, engine-truth tests, Action Kernel, business action handoff, and connector-health control.
- Full repository suite and live runtime are still not certified. In particular, this source-level patch does not establish which concrete engine instances are active in deployment or prove that any production engine publishes `metrics.director_signals/v1`.

## Immediate next task

Inventory the real lifecycle/container registrations against `ENGINE_REGISTRY` and `CANONICAL_ENGINE_DEPENDENCIES`, then wire domain-specific, source-owned recommendations only where a real evidence contract exists. Do not synthesize health or recommendations to make the integration matrix look complete.


## Continuation — runtime engine-discovery diagnostics (2026-10-20)

- `auto_discover_engines()` now records each discovered observer and makes missing exports, import failures, and configured-factory failures explicit in `Director.status().diagnostics.engine_discovery`.
- Discovery continues to avoid invoking arbitrary callables; only the explicit getter allowlist is invoked. Plugin observer names are included in the report.
- Added tests for missing-export and import-failure reporting. Combined focused regression suite passed **116 tests**; Python compilation passed.
- The report is based on the current startup attempt, not proof that an observer's health contract is authoritative or that live deployment has identical wiring.

## Continuation — discovery requires a real instance (2026-10-20)

A local source-checkout run against the 18-entry engine registry yielded 8 discovered object/factory observers, 3 missing exports, 7 unsupported class/function exports, and no import/factory failures. This run did not connect to or inspect the deployed service runtime.

Discovery now explicitly excludes non-allowlisted callables and classes from observer registration rather than registering a function/class as if it were a live engine instance. It records each case in `unsupported_exports`, including module, export, kind, and a reason that a live instance or explicit adapter is required. Arbitrary classes are not instantiated and request handlers are not called. A regression test verifies both non-invocation and visible diagnostics.

Validation: all 83 tests in `tests/test_director*.py` passed, and the changed Python files compile. This batch improves the truthfulness of discovery; it does not itself resolve the 3 missing exports or 7 unsupported targets. The next implementation pass must map lifecycle-owned instances into explicit adapters using the service container, with health contracts tested per adapter, before attempting engine recommendation publishers.
