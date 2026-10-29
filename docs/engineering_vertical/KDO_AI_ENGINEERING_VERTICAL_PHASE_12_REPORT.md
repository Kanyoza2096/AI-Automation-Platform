# KDO AI Engineering Vertical — Phase 12 Report

## Delivered

Phase 12 adds `services/kdo_engineering_governed_actions.py`, a narrow governed-action bridge that:

1. Requires tenant, workspace, and task scope before contacting governance or execution.
2. Requires a registered Action Kernel contract before Council deliberation.
3. Uses KDO's configured `council.integration.deliberate_action` by default; an injectable callback exists for deterministic tests.
4. Sends the exact action name and task/agent/tool scope in the Council proposal.
5. Requires explicit `is_approved is True` and a Council-issued non-empty decision ID. A caller-supplied `decision_id` in model/tool parameters is discarded.
6. Calls the real `ActionKernel.execute` boundary with the Council decision, workspace scope, correlation identity, and idempotency key.
7. Treats missing/uncertain kernel results as non-success and requires a separately configured receipt verifier before returning `succeeded`.
8. Records scoped proposal, approval, denial, and receipt-verification events in the development event store when configured.

## Validation

The focused regression command was:

```text
pytest -q tests/engineering_vertical tests/reliability/test_execution_ledger.py tests/test_condition_engine_council_approval_fence.py
```

Result: **104 passed** (includes engineering vertical tests, execution-ledger reliability tests, and Council approval-fence tests).

Python compilation and archive integrity checks are also performed before packaging.

## Important limitations / release blockers

- The repo's `ExecutionLedger` exposes mutation/lease lifecycle operations but does not expose a canonical receipt lookup/verification API suitable for this bridge. Consequently, a deployment must inject `receipt_verifier` backed by its authoritative ledger/read model. Without it, the bridge deliberately returns `uncertain`, never success.
- The production Council path is wired as the default call but was not exercised against a live deployed Council service. Tests replace the deliberation call with deterministic fixtures.
- The Action Kernel end-to-end test uses an in-memory ledger for test isolation. That is not evidence of durable production execution; consequential production actions must use the configured durable ledger and fail closed when unavailable.
- Team event storage remains a local SQLite development adapter and is not a signed receipt store.
- Production workspace sandboxing, durable distributed worker leases, crash recovery, and independent review integration remain outstanding.

## Release posture

**Not production-certified.** Phase 12 proves the integration seam and fail-closed behavior in tests. It does not claim a live provider, live Council deployment, or authoritative durable receipt verification is operational in production.
