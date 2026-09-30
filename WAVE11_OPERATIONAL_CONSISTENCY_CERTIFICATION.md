# KDO Wave 11 — Operational Consistency Certification

## Objective

Make the durable execution ledger the single source of operational truth for
reconciliation requests, incident signals, and Mission Control telemetry.

## Implementation

`services/execution_operational_state.py` adds a provider-neutral projection
from an authoritative `ExecutionRecord` to `ExecutionOperationalState`.

The projection derives:

- canonical status and health
- provider receipt identity
- reconciliation requirement
- incident requirement
- Mission Control telemetry
- reconciliation request payload
- incident signal payload

`ExecutionLedger.operational_state()` exposes this projection without creating a
second execution state machine.

## Safety invariants

1. `succeeded` without a provider object ID is downgraded to `uncertain`.
2. `uncertain` requires reconciliation and emits an incident signal.
3. `failed` emits an incident signal but does not automatically request provider
   reconciliation.
4. Reconciliation and incident payloads share the same execution ID and
   idempotency key.
5. `cancelled` does not create an incident or reconciliation request by itself.
6. No provider-specific logic is introduced into the ledger projection.

## Validation

- 22 focused execution/reconciliation/architecture tests passed.
- Python compilation passed.
- The projection is derived directly from `ExecutionRecord`; no competing
  operational state store was introduced.
