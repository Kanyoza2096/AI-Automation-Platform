# Phase 37.4 — Production Hardening + Final Validation

## Objective

Converge the integrated organism into a release-ready, bounded architecture without creating a second authority plane.

## Hardening delivered

- Added `organism.hardening` as a dependency-light integrity boundary for completed organism cycles.
- Added bounded collection checks for observations, learning, predictions, dreams, capability checks, homeostasis and immune signals.
- Added tenant-consistency checks across state, observations, learning, evolution, capabilities and cognitive decisions.
- Added declarative metadata validation for obvious credential, permission, authority and executable-code channels.
- Integrated an immutable `IntegrityReport` into every completed synchronous organism cycle.
- Invalid derived cycle state fails closed rather than being returned as trustworthy organism state.
- Async lifecycle continues to reuse the same integrity-checked composition boundary.

## Authority invariants

The organism remains downstream of authoritative platform planes:

1. Durable execution ledger owns external execution truth.
2. Guardian and DecisionGate own safety/governance decisions.
3. Credentials and provider connectors remain outside organism learning, dreaming and genome state.
4. Predictions and dreams remain non-authoritative.
5. Genome evolution is declarative and bounded; it cannot generate production code.
6. Ecosystem signals remain advisory, tenant-scoped and explicitly opt-in.
7. Integrity validation does not grant authority; it only rejects malformed or unsafe derived state.

## Validation

- Full pytest suite: **200 passed, 1 existing deprecation warning**.
- Python compilation: **PASS**.
- Migration structure: **39 SQL files, PASS**.
- Docker build-context secret/test exclusion: **PASS**.
- Production artifact gate: **PASS**.

## Deployment boundary

This is a static artifact certification. Real staging remains required for database/RLS behavior, Redis coordination, provider APIs, Docker build/runtime, external webhook flows, and production credentials.
