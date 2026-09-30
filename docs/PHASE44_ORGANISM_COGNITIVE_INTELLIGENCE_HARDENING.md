# Phase 44 — Organism/Cognitive Intelligence Hardening

Status: implemented and test-verified.

## Objective

Turn the Organism/Cognitive layer from a collection of bounded primitives into a more truthful learning loop without allowing derived intelligence to become execution authority.

## Changes

1. **Execution-feedback identity**
   - `ExecutionFeedback` can carry an explicit `goal_id`.
   - Lifecycle resolves feedback to a workspace-local goal and rejects unknown goal identities.
   - Learning preserves goal identity in episodic experience.

2. **Replay-safe learning**
   - Episodic learning now deduplicates records by authoritative `execution_id`.
   - Replaying a durable receipt no longer creates duplicate experience memory.

3. **Honest strategy evolution**
   - `GenomeEngine.evolve()` can generate bounded declarative variants from an explicit action pool.
   - Runtime exposes `strategy_action_pool` so “evolution” can actually create candidates rather than merely rename candidate evaluation.
   - Candidate deduplication uses immutable genome IDs rather than hashing mutable mappings.

4. **Dreaming uncertainty**
   - Repeated actions in a simulated sequence receive a small bounded uncertainty penalty.
   - The simulator does not claim to know transition dynamics or causal effects.

## Findings that remain deliberately unresolved

- `WorldModel` is still process-local and is a derived knowledge cache, not a durable enterprise knowledge graph.
- `CausalHypothesis` remains observational association, not causal inference.
- Perception source authenticity is not yet a full trust/attestation layer; callers must provide authoritative observations.
- Dream simulation is still mostly per-action and does not model learned state transitions.
- Organism memory/world-model learning is not yet calibrated against long-run production outcomes.

These are not hidden behind naming: they remain explicit staging/engineering gaps.

## Verification

The focused Organism/Cognitive tests pass, and the full repository suite passes after this phase.
