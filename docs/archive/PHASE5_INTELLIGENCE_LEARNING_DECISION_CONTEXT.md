# Phase 5.4 — Intelligence Learning Decision Context

## Boundary

`Append-only Learning Feedback -> Bounded Advisory Decision Context -> Decision Engine`

The context projection is read-only and advisory. It does not become policy,
authorization, autonomy, approval, ActionIntent, execution, provider state, or
historical truth.

## Guarantees

- only workspace-matching Phase 5.3 feedback is accepted;
- feedback is bounded to ten items per projection;
- duplicate evidence fingerprints are removed;
- uncertain learning remains uncertain;
- source evidence fingerprints are preserved;
- deterministic context identity is derived from the selected evidence;
- action, policy, approval, autonomy, governance, and execution overrides are forbidden;
- causal claims and historical mutation are forbidden;
- authority is always `none`.

A future Decision Engine may consume this object as advisory evidence. The
projection deliberately contains no field capable of silently changing the
engine's policy or execution behavior.

## Runtime continuity proof

The architecture suite includes a restart test that writes Phase 5.3 feedback
to the real SQLite learning store, closes it, reopens it, projects the recovered
rows into the Phase 5.4 context, and validates the resulting context. This proves
the Decision Engine boundary can consume durable learning evidence rather than a
process-local cache.
