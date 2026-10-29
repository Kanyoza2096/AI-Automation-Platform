# Kanyoza P30 — Durable Execution Gate and Honest Outcome Normalization

**Date:** 2026-10-22  
**Scope:** Condition Engine governed execution adapter; no live external effects.

## Executive result

P30 hardens the approved-action path so consequential content mutations cannot silently use an in-memory execution ledger, and so the Condition Engine does not report an unverified executor result as success.

## Changes

1. **Durable ledger required for governed content mutations**
   - The Condition Engine integration now returns `durable_execution_ledger_unavailable` before constructing the executor or calling the content mutation if no persistence client is configured.
   - The ledger is instantiated with `allow_in_memory=False`; a configured client is not treated as proof that durable writes work, so actual persistence still needs staging verification.

2. **Outcome normalization**
   - `status=succeeded` is only normalized to `ok=true` when a provider object ID is present.
   - `uncertain` and `in_progress` are never surfaced as success.
   - A malformed succeeded result without a provider object ID is downgraded at the integration response boundary to an uncertain/reconciliation-required result.

3. **Regression tests**
   - Added tests that verify no durable ledger means no executor construction.
   - Added a malformed success result test.
   - Updated the approved path test to provide a configured ledger client and assert in-memory fallback is disabled.
   - Existing unknown-Council approval test continues to verify fail-closed behavior.

## Validation

- Focused governance/readiness/execution suite: **83 passed, 0 failed**.
- Meta/Condition in-process audit: **11/11 checks passed**.
- Python compileall: exit code 0.
- ZIP integrity: verified after packaging.
- The Python environment emitted a spreadsheet warmup diagnostic during startup; it did not change the compile or test exit codes.

## Safety and limitations

Tests use fake Council decisions, a fake content engine, and fake executor responses. No live Council, Supabase/Redis service, content provider, or real action executor was invoked. The audit remains local and read-only.

A configured database client does not prove durable receipt writes, and this integration's receipt/status shape is not an independent postcondition check. Isolated staging must verify: actual database writes and reads; rejected/unknown decisions produce no effects; idempotency across process restart; uncertain outcome reconciliation; and provider object identity/postcondition verification.
