# KDO Director Orchestration — Phase 0 Audit

**Audit date:** 2026-11-18  
**Branch:** `feat/director-orchestration-architecture`  
**Status:** Read-only source and database audit; no migrations or runtime changes performed.

## 1. Environment and persistence findings

### Staging database

Project: `kkzshurcmcptleyqjgzo`

Read-only inspection found:
- `director_tasks`: 571 rows — 526 completed, 44 failed, 1 flagged.
- `director_strategic_plans`: 1 proposed plan.
- Both tables have RLS enabled; direct SELECT is denied to `anon` and `authenticated`, and available to `service_role`.
- `director_mission_admissions` does not exist.
- `director_collaboration_runs` does not exist.
- `director_engineering_events` does not exist.
- No `stage_director_mission` RPC is installed.

Staging Director task columns differ from production in nullability/defaults: for example, `workspace_id` and `status` are nullable in staging, and `director_tasks.status` has no default in the inspected schema. Do not assume production's stricter contract exists in staging.

### Production database

Project: `kfgutijhrywnxpsjjacd`

Production contains:
- `director_tasks`, `director_strategic_plans`;
- `director_mission_admissions` and the `stage_director_mission` RPC;
- `director_collaboration_runs`;
- `director_engineering_events`.

The collaboration table has RLS enabled and direct `anon`/`authenticated` table access revoked; backend `service_role` access is granted.

Two production collaboration records exist. Both are completed but both selected only `content`, with one consultation each. This confirms persisted advisory runs, not multi-engine execution.

### Migration-history divergence

Staging's Director history includes:
- `staging_director_persistence_contracts`;
- `u2_director_strategic_plans`;
- `director_state_scope_classification`.

Production's Director history includes separately named/ordered migrations for:
- mission admissions;
- strategic plans;
- mission RPC task schema;
- collaboration runs;
- Director state scope classification;
- engineering change ledger;
- website event idempotency/context snapshots.

The staging-specific migration `staging_director_persistence_contracts` has no matching source migration file discoverable in the audited backend repository. The repository contains production-oriented migration files 153–157 and 163–171, but staging has already applied some equivalent schema under different migration names.

**Additional critical finding:** the production `stage_director_mission` RPC inserts workspace-owned tasks without setting `scope_type`. Migration 170 requires `scope_type = 'workspace'` whenever `workspace_id` is non-null, while the column default is `platform`. The current production function definition therefore conflicts with the live table constraint and is expected to fail on a new workspace mission admission. This was established by comparing the live function definition with the live check constraint; the RPC was not invoked and no rows were changed.

**Staging remediation applied and verified:** migration `director_mission_admission_scope_contract` (repository file `migrations/175_director_mission_admission_scope_contract.sql`, Supabase version `20261118200918`) created the missing admission receipt table and installed a `SECURITY INVOKER` RPC that explicitly inserts `scope_type = 'workspace'`. RLS is enabled; `anon` and `authenticated` have no table SELECT or function EXECUTE; `service_role` has the required access. A transactional test invoked the function twice to verify initial admission and idempotent replay, then deliberately rolled the test transaction back. No test rows remain. Staging still has exactly 571 Director tasks, 1 proposed plan, and 0 admission receipts. No existing rows were changed.

**Production remains untouched:** its live RPC still omits `scope_type`, conflicting with the live `director_tasks_workspace_scope_check` constraint and the `platform` default. This is a source/schema-confirmed production defect that requires a separate forward-only production migration after the staging fix is merged and reviewed.

**Decision:** do not replay migrations 153–157 wholesale into staging. Use migration 175 as the forward-only staging contract and keep the separate production RPC repair behind the normal release gate.

## 2. KCPG command boundary

The frontend repository defines a typed `KCPGCommandDescriptor` and typed command transaction contract in `src/lib/kcpgDomainContracts.ts`. The generated command list identifies `gateway/contracts/control-plane-command-registry.v1.json` as its source. The frontend API client dispatches through the separately configured KCPG REST origin:
- `GET /commands` for the live registry;
- `POST /commands/{commandId}` for dispatch;
- transaction read/approval endpoints under `/commands/.../transactions`.

The audited backend repository contains Director APIs and read-only AI Control Plane projections, but no matching KCPG `/commands` registry/dispatch implementation was found. Therefore the browser-side descriptor is not the authoritative runtime implementation, and the server-side KCPG authorization/dispatch path cannot be fully certified from these two repositories alone.

**Integration rule:** the Director orchestration kernel must not bypass KCPG. It must call the same server-side command/capability boundary used by approved workflows. The KCPG gateway source repository or its versioned OpenAPI/command-contract package is a required integration dependency before end-to-end governed dispatch is certified.

## 3. Existing engine invocation candidates

### Suitable first adapters, subject to focused contract tests

1. **Meta Engine** — native methods include fresh health observation and diagnosis. Use health/diagnosis and dependency evidence only. Recovery methods (`_perform_recovery`, `_start_recovery`, recovery handlers) must not be reachable from advisory collaboration.
2. **Condition Engine** — `observe(Observation)` performs native deterministic analysis and records its decision. Its plan execution methods (`execute_plan`, `execute_plan_async`) are a separate boundary and must not be called by the advisory adapter. The adapter must require structured, workspace-scoped observations and must preserve the distinction between a recommendation and an executed action.
3. **Content Intelligence Engine** — `ContentIntelligenceEngine.analyze(..., workspace_id=...)` is a native research-oriented path with explicit workspace context. Validate its provider/web-source budgets, evidence references, and side-effect profile before registering it as a task adapter.

### OODA and Guardian constraints

- OODA's public `get_status` is read-only status, not substantive mission analysis. The autonomous loop's `_ooda_cycle` includes an ACT phase and must not be called by an advisory adapter. A dedicated non-mutating OODA analysis contract is needed before it can be counted as a native specialist worker. Its model/provider path must propagate workspace budget context.
- Guardian `scan_all` is not a pure read-only consultation: it can clone/pull repositories, scan files, run auto-fix paths, record analytics, and publish events. Do not use it as a generic advisory adapter. It needs a separately scoped read-only scan mode and explicit side-effect controls.

### Engineering team clarification

`services/kdo_engineering_agent_team.py` provides role assignment, dependency ordering, contribution validation, and evidence fingerprints. Its own contract explicitly says it does not claim provider integration, code execution, parallel execution, approval, or release. It is a useful coordination primitive, but it is not yet a universal native-engine execution runtime.

## 4. Reusable orchestration foundations

- Director mission/task DAG, task store, restart restoration/reconciliation, and bounded scheduler in `services/director/core.py` and `services/director/stores.py`.
- Existing mission admission RPC in production; not present in staging.
- Capability bindings in `services/director_bindings.py`, with real service calls for selected non-critical capabilities and governed handoffs for protected operations.
- Action Kernel contracts, workspace scoping, approval/risk checks, idempotency and durable outbox boundary in `services/action_kernel.py`.
- Unified Event Bus provides correlation IDs, event persistence, retry/dead-letter handling and timelines. Reuse it where its event semantics fit; do not confuse event delivery with a durable mission aggregate.
- AI Gateway `_Budget` is keyed by workspace and uses Redis when available, with process-local fallback. Default daily budget is configured by `LLM_DAILY_TOKEN_BUDGET` (default 500,000). AI route/provider selection is centrally owned by `services/ai_policy.py`.
- Existing Director scheduler has `MAX_CONCURRENT_EXECUTIONS = 5`; mission admission RPC contract caps a staged mission at 12 tasks. Existing collaboration selector is capped at four engines.

## 5. Required Phase 0 closure items

1. Obtain/identify the KCPG gateway source contract and verify the live registry/dispatch/approval path.
2. Reconcile staging Director persistence using a forward-only migration with exact parity checks, preserving current task and plan records.
3. Add a typed native-engine adapter contract and readiness evidence. Do not use observer discovery or an LLM persona label as proof of executable capability.
4. Implement focused read-only adapter tests for Meta, Condition, and Content Intelligence; then add OODA only after a safe non-mutating analysis method and budget propagation are explicit.
5. Define the durable mission aggregate and append-only message/event schema only after existing Event Bus and Director event stores are checked for reusable contracts.
6. Prove actual native method invocation, durable task progress, and restart recovery in staging before production rollout.

## 6. Safety and change record

- Database inspection was read-only.
- No staging or production migrations were applied.
- No production rows were changed.
- No backend/frontend runtime code was changed.
- No deployment was triggered.
- PR #349 remains a draft architecture-only PR.
