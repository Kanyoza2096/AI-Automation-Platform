# Kanyoza P28 — Meta/Condition Engine Readiness Remediation

**Date:** 2026-10-22  
**Scope:** Condition Engine health/readiness contract, API health reporting, lifecycle executor-binding evidence, and Meta Engine readiness visibility.

## Executive result

P28 implements explicit capability-readiness reporting without conflating analysis availability with action execution or durable persistence. The Condition Engine remains able to analyze observations even when no action executor is installed, but now reports `execution_ready: false` and the `action_executor_unavailable` limitation. This preserves the existing fail-closed behavior and avoids incorrectly implying that a callable analysis engine can perform governed actions.

## Code changes

1. `services/condition_engine/core.py`
   - Added `readiness_report()` with separate `analysis_ready`, `execution_ready`, and `persistence_ready` fields.
   - Distinguishes an explicit non-executing fallback from a usable executor.
   - Tracks executor binding evidence as `bound`, `unavailable`, or `unverified`.
   - Does not equate a configured persistence client with proven durable writes. Persistence remains unverified (`persistence_status: unknown`) when configured, and unavailable when no client exists.
2. `services/condition_engine/api.py`
   - API `health_check()` now delegates to the core health contract and includes readiness evidence rather than hardcoding `healthy`.
   - Marks the default no-op executor as explicitly unavailable for readiness assessment; its behavior remains fail-closed.
3. `lifecycle.py`
   - Records initial executor-binding state and updates it after the Council-governed binding attempt.
   - On binding failure, explicitly records `unavailable` and reinstalls the fail-closed executor.
4. `services/meta_engine/core.py`
   - Adds per-engine `engine_readiness` to Meta Engine health reports when an engine exposes readiness data.
   - Keeps capability readiness separate from Meta's liveness/overall status, avoiding a false claim that analysis itself is unavailable just because execution is not wired.
5. Added regression tests:
   - `tests/test_condition_engine_readiness_contract.py`
   - `tests/test_meta_engine_readiness_visibility.py`
6. Updated `scripts/audit_meta_condition_engines.py` to validate the new contract and report remaining limits accurately.

## Validation results

- Focused regression selection: **50 passed**.
- In-process engine audit: **10/10 checks passed**.
- Python compilation (`compileall`) for changed Python modules and tests: **passed**.
- ZIP archive integrity: verified separately after packaging.

A broader exploratory selection that included `tests/test_engine_fitness_contracts.py` produced **57 passed, 3 failed**. Those three failures are outside the changed code paths: two tests expect flat modules `services/director.py` and `services/meta_engine.py` that do not exist in this repository layout, and one expects `complete_async()` in `services/content_engine.py`. These failures are recorded, not represented as passing tests.

## Not certified by this iteration

- Live startup and Council-governed executor binding in the deployed runtime.
- Supabase/other persistence durability, durable action receipts, or read-after-write verification.
- Real external side effects and authoritative postcondition verification.
- Live cross-service event delivery or production readiness.

The audit and tests are local/in-process. No external services were contacted and no action executor was invoked by the audit. A callable executor is reported as callable, but its governance and real-world outcome still require runtime/staging verification; lifecycle binding evidence is surfaced separately for that reason.

## Recommended next verification

In isolated staging, verify: lifecycle installs the Council-governed executor; a representative observation is ingested; a proposed action is approved through the governance boundary; an authorized test action produces a durable receipt; and the authoritative postcondition is observed. Only then should execution and persistence be certified operational.
