# Receipt Propagation Invariant

**Status:** Governing architectural invariant for canonical external execution.

## Definition

Once an external provider produces an authoritative receipt, downstream KDO
layers may normalize its representation but must not silently discard its
identity, status, or provenance.

An authoritative provider success therefore propagates as:

```text
Provider Receipt
      ↓
Canonical Receipt
      ↓
Canonical Outcome
      ↓
Execution Ledger
      ↓
Workflow / Domain State
      ↓
Operational Projection / Mission Control
```

An uncertain provider response cannot be silently upgraded to success. A
successful response without the provider object identity required by the
provider contract is classified as **uncertain** and enters reconciliation.

## Canonical receipt protocol

The connector fabric exposes a sector-neutral `ProviderReceipt` containing:

- provider
- status
- provider object identity
- provider request identity
- observed object identity for uncertain responses
- error
- provider metadata/evidence

Sector packs and provider adapters remain free to use their own vocabulary at
the edges, but the canonical execution boundary normalizes it into this
protocol.

## Connector Fabric responsibilities

The fabric is intentionally divided into three concerns:

1. **Receipt Protocol** — sector-neutral evidence contract.
2. **Action Contracts** — governed actions declared by sector/domain packs and
authorized by KDO governance.
3. **Provider Adapters** — vendor-specific API integration.

No connector or sector pack becomes a second execution authority.

## Failure semantics

```text
provider success + authoritative ID  → succeeded
provider success + missing ID        → uncertain
transport ambiguity                  → uncertain
provider definitive rejection        → failed
uncertain                            → reconciliation
```

This invariant prevents the class of failure in which the external side effect
succeeds but a downstream workflow incorrectly reports failure because the
provider receipt was lost or renamed between layers.
