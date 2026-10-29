# KDO AI Engineering Vertical — Phase 14 Report

## Delivered

Phase 14 adds a read-only staging preflight and a lifecycle regression harness.

- `services/kdo_engineering_staging.py` performs zero-row queries against the canonical `action_outbox` and `external_execution_ledger` receipt columns. It never mutates data and returns sanitized blockers instead of leaking database errors.
- The preflight fails closed if a database client is missing, a query fails, or the client cannot perform zero-row selects.
- The preflight explicitly reports its limitations: a successful schema/read-access check does not prove tenant-specific row visibility and does not execute live Council or Action Kernel workflows.
- `tests/engineering_vertical/test_staging_lifecycle.py` covers schema preflight, missing database access, incomplete scope, Council denial, an actual in-memory `ActionKernel` execution with receipt verification, duplicate request replay using the same idempotency key, and an interrupted/uncertain outcome.
- The duplicate-request test asserts the handler side effect occurs once and the replay returns the same execution identity.
- The uncertain-outcome test ensures a simulated interrupted provider call is not upgraded to verified success.

## Validation

Command:

```text
pytest -q tests/engineering_vertical tests/reliability/test_execution_ledger.py tests/test_condition_engine_council_approval_fence.py
```

Result: **117 passed**.

Python compilation and ZIP integrity checks were also run during packaging.

## Staging execution status

**Live staging has not been executed from this environment.** No staging credentials or deployed Council/database endpoints were supplied, so the package must not claim that production RLS, live service behavior, or provider side effects have been validated.

Before release, an operator with staging access must:

1. Run the read-only schema preflight using the same least-privilege database role used by the receipt verifier.
2. Confirm tenant/workspace RLS with real scoped records, including negative cross-tenant reads.
3. Run the dedicated non-destructive staging probe through the deployed Council and Action Kernel.
4. Verify the returned receipt against both canonical durable records.
5. Repeat the same idempotency key and confirm the external handler is not invoked twice.
6. Exercise denial, missing receipt, interrupted execution, and reconciliation-required outcomes.
7. Preserve staging logs and canonical receipt IDs as release evidence.

## Release posture

**Not production-certified.** Phase 14 adds executable CI coverage and a read-only staging preflight. It does not substitute for live staging validation, row-level security testing, production worker recovery, sandbox validation, or independent code review.
