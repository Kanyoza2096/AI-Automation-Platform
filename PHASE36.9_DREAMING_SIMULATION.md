# Phase 36.9 — Dreaming & Simulation

Kanyoza now has a bounded offline simulation plane. A `DreamEngine` evaluates hypothetical action sequences using tenant-scoped learned predictions without executing actions or mutating production state.

## Safety boundary

- no connector/provider calls
- no execution-ledger writes
- no credentials or authority changes
- no goal mutation
- no production world-model writes during simulation
- bounded sequence length (20 steps)
- tenant mismatch rejected
- uncertain outcomes remain uncertain and are penalized rather than promoted

Dreams are recommendations/evaluations, not execution plans with implicit authority. A later cognitive/governance layer must explicitly approve any real action.
