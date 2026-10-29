# Phase 5.2 — Intelligence Outcome Attribution Gate

This boundary closes the post-execution Phase 5 lineage without allowing intelligence to become an outcome authority.

## Canonical chain

`ActionIntent Admission → Authorization → Action Kernel → Execution Ledger → Provider Receipt → Authoritative Verification → Terminal Reconciliation → Business Outcome → Attribution`

## Admission requirements

Attribution is accepted only when:
- the execution carries the exact intelligence provenance envelope;
- provider receipt is authoritative (`succeeded` with a provider object identity);
- authoritative verification is terminal/verified and references the execution/provider identity;
- reconciliation is terminal and references the same execution;
- the business outcome references the same execution/reconciliation;
- all evidence is tenant/workspace scoped.

Uncertain provider receipts, non-terminal reconciliation, forged admission fingerprints, provenance mismatch, provider identity mismatch, and workspace mismatch fail closed.

## Authority boundary

The attribution result is read-only evidence. It cannot authorize, execute, retry, reconcile, or rewrite provider truth. It explicitly records `causal_claim=false` and `action_authority_granted=false`.

## Outcome semantics

Verification and reconciliation determine whether the external outcome is authoritative. Intelligence provenance explains where the action originated; it never determines whether the action succeeded.
