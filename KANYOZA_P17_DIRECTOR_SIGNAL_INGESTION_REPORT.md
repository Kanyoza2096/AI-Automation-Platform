# Kanyoza P17 — Director Signal Ingestion Hardening

## Goal
Trace the actual hand-off from engine health publishers to Director observations and make contract coverage accurately distinguish versioned publishers from legacy signal-shaped metrics.

## Findings
- `FunctionObserver.observe()` carries an engine health result's `metrics` into a Director `Observation`.
- `Director._observe_async()` stores these observations and makes them the current observation set.
- `Director._engine_decision_signals()` consumes `Observation.metrics.director_signals` and applies freshness, health, sector, capability, and input validation before creating advisory tasks.
- P16's three publishers currently expose the contract but publish an empty recommendation list. This phase does not invent engine-specific recommendations without verified diagnostic evidence.

## Changes
- Contract coverage now requires both a list-valued `director_signals` field and the exact `metrics.director_signals/v1` marker. A legacy list is still accepted for backward compatibility but is no longer counted as a versioned publisher.
- An explicitly declared unsupported contract version is rejected rather than silently interpreted as the current contract.
- Signal batches above the 50-item limit are rejected as a whole, rather than silently truncating and making coverage appear complete.
- Added regression tests for legacy coverage, unsupported contract versions, and oversized batches.

## Validation
- Compilation: passed.
- Focused regression suite: **132 passed**.
- ZIP integrity: verified after packaging.
- Production observer ingestion, deployment wiring, and Supabase connectivity were not tested against a live environment.

## Next phase
Add evidence-backed recommendations from concrete diagnostics, only where the source metrics prove the condition and the Director capability/sector/authority contract can be satisfied. Then validate the full publisher-to-observer path with integration tests using the actual registered observer wiring.
