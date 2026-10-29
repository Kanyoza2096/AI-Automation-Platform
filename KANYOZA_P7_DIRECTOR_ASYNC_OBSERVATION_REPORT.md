# Kanyoza P7 — Director Async Health Observation

## Finding

Director's observer adapter only tried synchronous `get_status()` and `health_check()` methods. If an engine exposed an asynchronous health method, calling it could produce a coroutine that Director never awaited; the observation then fell through to `unknown`, and could emit an un-awaited-coroutine warning. Meta Engine already supported asynchronous health contracts, so the two supervisory paths did not have equivalent evidence coverage.

## Change

- `services/director/core.py`
  - The observer adapter now checks `check_health_async` first, then `health_check`, then `get_status`.
  - Awaitable health results are resolved with `asyncio.run()` in Director's executor-thread observation path.
  - If called synchronously from a thread that already has a running event loop, the awaitable is closed when possible and the adapter fails closed rather than nesting event loops or leaking a coroutine.
  - Payloads without an explicit status remain `unknown`; async support does not manufacture a healthy verdict.
- `tests/test_engine_truth.py`
  - Added async healthy evidence and async payload-without-status regression tests.

## Validation

- Compilation of changed modules/tests: passed.
- Focused Director / topology / route / health-truth suites: **113 passed**.
- The previously observed full-suite blocker remains: `tests/pipeline/test_publish_end_to_end_contract.py::test_connector_receives_brand_for_tenant_specific_facebook_credentials` requires the missing `supabase` Python package. No claim of a green full suite or production readiness is made.

## Remaining integration boundary

Async observation is now supported for health methods that return the documented dictionary contract. The next step is to compare Director's observations against Meta Engine's verified health snapshot and dependency graph, preserving timestamp, source, and confidence rather than merging the two signals as if they were identical.
