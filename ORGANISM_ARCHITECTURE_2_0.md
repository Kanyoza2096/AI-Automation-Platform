# Kanyoza Organism Architecture 2.0

## Purpose

Kanyoza evolves from a collection of automation engines into a coherent, tenant-scoped digital organism without multiplying subsystems unnecessarily. The design principle is **convergence before expansion**: existing engines remain authoritative where they already own a capability; new code is added only where a missing shared primitive creates measurable leverage.

## 1. Canonical architecture

```text
External world / tenant inputs
          |
          v
      PERCEPTION
          |
          v
     WORLD MODEL <------ MEMORY
          |
          v
     GOALS / INTENT
          |
          v
  ORGANISM STATE + PREDICTION
          |
          v
   COUNCIL / DECISION
          |
     +----+----+
     |         |
     v         v
HOMEOSTASIS  GUARDIAN / IMMUNE
     |         |
     +----+----+
          |
          v
     DURABLE ACTION
          |
          v
   EXECUTION LEDGER
          |
          v
 External provider
          |
          v
       RECEIPT
          |
          +------> WORLD MODEL / MEMORY / LEARNING
```

The existing durable outbox, execution ledger, event bus, governance, MetaEngine, organism registry, metabolism, dreams and learning systems are the foundation. The new architecture gives them a shared vocabulary and authority model.

## 2. Existing engines mapped to organism concepts

| Organism capability | Existing authority | 2.0 direction |
|---|---|---|
| Species/runtime supervision | MetaEngine, Director, engine topology | Canonical system truth; no competing health truth |
| Tenant organism | `core.organism.Organism`, `organism.registry` | Tenant-local coordinator/state owner |
| Council / cognition | Council / DemocracyEngine | Preserve disagreement, evidence and risk reasoning |
| Memory | `core.memory`, organism memory | Layer episodic/semantic/procedural interfaces over existing stores |
| Dreams | `core.dreams` | Offline simulation only; never direct production authority |
| Metabolism | `core.metabolism` | Resource budgets for compute, AI, API, publishing and workers |
| Reflexes | `core.reflexes`, decision gates | Fast deterministic safety path before expensive cognition |
| Guardian / immune | Guardian + security controls | Detection, containment, quarantine, recovery and immune memory |
| Durable action | Publish outbox + ExecutionLedger + governed connectors | Authoritative action lifecycle |
| Event nervous system | UnifiedEventBus | Canonical internal signal transport |
| Autonomy | AutonomousLoop / AutonomousContent | Goal-directed execution constrained by governance and budgets |
| Learning | outcome feedback / learning services | Update models and strategies, not executable source code |

## 3. New shared primitives

### Organism State

One typed snapshot answers: What is the organism doing, how healthy/certain/capable is it, what objective is active, what resources and risks constrain it, and what does it currently predict?

The state is a **derived truth-plane snapshot**, not a second database of every subsystem's data. Subsystems remain authoritative for their own records.

### World Model

The world model stores observations and beliefs with provenance, confidence, freshness and contradiction status. It must distinguish:

- observed fact
- inferred belief
- prediction
- uncertainty
- contradiction
- stale knowledge

Provider responses are observations, not unquestioned truth.

### Goals / Intent

A goal contains desired state, priority, deadline, constraints, budget, authority level, success criteria, failure criteria and cancellation conditions. Goals are tenant-scoped and auditable.

### Homeostasis

Homeostasis continuously balances operational and cognitive resources: AI spend, provider quotas, worker capacity, latency, publishing frequency, error budget and attention. It may slow, defer or reject work, but cannot elevate privileges.

### Immune system

The immune layer turns Guardian signals into a controlled lifecycle: detect -> classify -> contain -> quarantine -> recover -> learn. It maintains immune memory so recurring failure patterns become easier to recognize.

### Genome

The genome is a versioned configuration/strategy set: capabilities, prompts/templates, policies, thresholds and strategy variants. Evolution is bounded experimentation. Production code and security controls are never self-modified by the organism.

### Prediction / causal layer

Predictions are explicitly probabilistic and traceable to observations and assumptions. Causal relationships are hypotheses unless supported by evidence. Predictions can trigger review or planning, not bypass governance.

### Skill registry

Skills are explicit capabilities with input/output contracts, cost, risk, required authority, tenant scope and health. A skill is executable only through the same governance and durable-action boundaries as any other action.

## 4. Authority boundaries

1. **Database durability is authoritative for durable execution state.**
2. **Internal event delivery is not proof of external success.**
3. **External providers are never authoritative for Kanyoza's internal lifecycle.**
4. **Higher-level supervisors consume lower-level health contracts; they do not invent health truth.**
5. **Every tenant-scoped state transition carries workspace identity.**
6. **Autonomy cannot grant itself permissions, credentials, budgets or new execution authority.**
7. **Ambiguous external outcomes remain `uncertain` until reconciled.**
8. **Dream/simulation results never directly mutate production state.**
9. **Genome evolution changes bounded strategies/configuration, not arbitrary executable code.**
10. **Human/operator controls remain capable of stopping autonomous execution.**

## 5. Cognitive control loop

```text
Observe -> normalize -> update world model -> evaluate goals
       -> predict -> select candidate actions -> Council
       -> Guardian / policy -> Homeostasis / budget
       -> durable outbox -> claim/fence -> execute
       -> receipt/reconciliation -> update world model
       -> outcome feedback -> memory/learning
```

Fast reflexes may short-circuit expensive cognition for known safe/unsafe patterns. High-risk actions always retain deterministic governance.

## 6. Failure model

The organism must distinguish:

- primary failure: authoritative dependency failed
- degraded dependency: capability unavailable because a dependency failed
- secondary cascade: downstream symptom
- uncertain: external outcome cannot be proven
- stale: information exceeded freshness policy
- unobservable: health could not be measured

This prevents dashboards and autonomous recovery loops from chasing symptoms.

## 7. Multi-tenancy and security

The organism is one logical mind per workspace, never one shared mutable mind across tenants. Shared infrastructure may be global, but mutable cognitive state, goals, memory, budgets, genome variants and action history are tenant-scoped.

All autonomous actions inherit the authenticated workspace context and the capability/authority boundary of the initiating goal. Cross-workspace access requires an explicit privileged control-plane path.

## 8. Implementation strategy

### Stage A — convergence
- Introduce the canonical Organism State contract.
- Establish the state as a derived snapshot composed from authoritative subsystems.
- Add contract tests for tenant identity, serialization and bounded values.
- Keep existing engines unchanged unless required for integration.

### Stage B — world model and goals
- Add observation/belief records and goal contracts.
- Connect event bus inputs without making events durable truth.
- Add freshness/confidence semantics.

### Stage C — homeostasis and immune control
- Consolidate metabolism and Guardian signals.
- Add resource-aware planning and quarantine state.

### Stage D — prediction, memory and dreaming
- Unify interfaces over existing memory/dream systems.
- Add simulation provenance and prediction confidence.

### Stage E — genome and skills
- Version bounded strategies and capability contracts.
- Introduce safe experimentation, lineage, fitness and rollback.

### Stage F — ecosystem
- Allow specialized organisms to cooperate through explicit contracts and least-privilege capabilities.
- Preserve tenant and control-plane boundaries.

## 9. Explicit non-goals

- No microservice split solely for conceptual purity.
- No giant autonomous agent framework.
- No arbitrary self-modifying production code.
- No direct provider calls that bypass durable execution/governance.
- No second health registry that competes with MetaEngine/topology.
- No unbounded event-driven feedback loops.
- No silent memory fallback in production for authoritative records.

## 10. Definition of exceptional

Kanyoza becomes exceptional when the organism can explain not merely **what** it did, but:

- what it believed about the world;
- why it selected a goal;
- which alternatives it considered;
- where Council members disagreed;
- what resources constrained the decision;
- which safety rules applied;
- what it expected to happen;
- what actually happened;
- how certain it is about that outcome;
- what it learned; and
- what it will change next time.

That is the target: **coherent intelligence with durable truth, bounded autonomy and observable reasoning**, not a larger codebase for its own sake.
