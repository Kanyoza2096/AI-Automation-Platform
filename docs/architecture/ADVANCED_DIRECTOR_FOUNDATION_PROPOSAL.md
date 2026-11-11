# Advanced Director Foundation — Architecture Proposal

**Status:** Proposed for architectural review only  
**Target:** KDO staging  
**Scope:** Design contract for the five foundational Director capabilities  
**Execution authority:** None  
**Implementation status:** Not started by this proposal

## 1. Purpose

Evolve KDO Director from a capable observer and planner into the strategic coordination layer of one coherent, adaptive KDO organism.

The proposal defines five connected capabilities:

1. **platform_state** — a shared, evidence-aware view of the organism.
2. **Service manifests** — a canonical registry of service ownership and contracts.
3. **Coordination bus** — typed, traceable events between subsystems.
4. **Capability statistics** — an evidence-derived reflex layer for capability readiness and reliability.
5. **Council integration** — the governance layer for consequential decisions.

These are not five independent products. They form one control loop: observe → understand → coordinate → evaluate capability → govern → learn.

## 2. Architectural decisions and invariants

- Extend canonical KDO components before creating parallel subsystems.
- Treat Director as the strategic coordinator, not the owner of specialist workflows.
- Keep probabilistic reasoning separate from deterministic state, authorization, execution, and audit.
- A plan, event, confidence score, manifest entry, or capability statistic never grants execution authority.
- Preserve tenant/workspace isolation and explicit platform-level scope.
- Every important state claim must expose source, timestamp, freshness, and whether it is observed, inferred, conflicted, or unknown.
- Reuse the existing unified event bus, Director evidence/context, organism state, Council, DecisionGate, workforce admission, and Action Kernel where they satisfy the contract.
- No new broker, paid worker, database project, or additional Supabase cost is proposed.
- No new database migration is authorized by this design. Durable storage changes require a separate reviewed implementation decision.
- Brand schedulers continue to own content generation and publishing. Director may observe, prioritize, diagnose, and delegate; it must not initiate publishing.
- The AI Software Engineering Vertical, Code Intelligence, engineering agents, planner, orchestrator, worker runtime, and governed execution boundaries remain protected.

## 3. The five capabilities

### 3.1 platform_state — shared organism state

**Responsibility:** Provide a consistent read model of what KDO is, what it is doing, what it can do, what is changing, and what remains uncertain.

**Design:**
- Compose state from authoritative subsystem records and typed events rather than creating a second source of truth.
- Identify each fact with subject, source service, source record/event, observed time, received time, freshness, workspace scope, and confidence/provenance class.
- Distinguish current observations from historical facts, inferred relationships, conflicting reports, and missing telemetry.
- Expose bounded projections for Director, Council, diagnostics, and operator interfaces; each consumer receives only the scope it is authorized to see.
- Support reconciliation against authoritative owners so missed or duplicated events do not permanently corrupt the read model.

**Reuse first:** Director organism_state and decision_context, existing decision/evidence stores, Meta Engine observations, intelligence evidence graph, and the current event infrastructure.

**Boundary:** platform_state is a read model and coordination context. It does not become a replacement database for subsystem-owned business records.

### 3.2 Service manifests — ownership and contract registry

**Responsibility:** Make service identity, responsibility, dependencies, and interfaces discoverable in a machine-verifiable way.

Each manifest should describe:
- Stable service identifier, name, owner, version, and lifecycle status.
- Responsibility statement and explicitly excluded responsibilities.
- Published capabilities and the canonical handler/owner for each capability.
- Consumed and emitted event types with schema versions.
- Health/diagnostic contract and evidence sources.
- Dependencies and dependency criticality.
- Workspace/platform scope and data classification.
- Autonomy boundary, required governance class, and human-approval requirements.
- Operational metadata such as retry/idempotency contract and recovery owner.

**Design rules:**
- Manifests describe ownership and contracts; they do not grant permissions.
- The registry validates references and schema compatibility at startup or CI, not by silently inventing missing services.
- Existing engine topology, route manifests, capability catalog, and runtime bindings should become validated projections or references—not competing registries.
- A service cannot claim another service's capability or silently assume its operational responsibility.

### 3.3 Coordination bus — typed event system

**Responsibility:** Provide reliable, observable coordination without tightly coupling services to each other's internals.

Reuse the existing unified bus and event lifecycle. Standardize a versioned envelope containing:
- event_id and event_type;
- schema_version;
- source_service and subject identity;
- workspace_id or explicit platform scope;
- occurred_at and recorded_at;
- correlation_id and causation_id;
- payload validated against the event schema;
- provenance and sensitivity classification.

**Required behavior:**
- Validate event type and schema version before dispatch.
- Make consumer delivery idempotent using event identity and consumer identity.
- Preserve correlation across fan-out, retries, and downstream events.
- Record delivery outcomes and distinguish retryable failures from permanent contract failures.
- Make duplicate, delayed, stale, and out-of-order delivery explicit.
- Provide bounded replay/reconciliation from existing durable evidence where supported.

Do not introduce a second event broker or silently create a new worker fleet. Any infrastructure change must be separately justified and approved.

### 3.4 Capability statistics — reflex and readiness layer

**Responsibility:** Tell Director which capabilities are available, reliable, constrained, or uncertain, using measured evidence rather than static declarations or self-reported success.

Candidate statistics:
- verified success and failure rates;
- unverified completion rate;
- latency distributions where measured;
- retry and recovery rates;
- timeout and contract-failure rates;
- current availability and dependency readiness;
- sample count, confidence/uncertainty, and last verified outcome time.

**Evidence policy:**
- Count positive reliability only after independent outcome verification.
- Keep handler acknowledgements and unverified completion separate from verified success.
- Do not infer a zero failure rate from missing telemetry or a zero sample count.
- Apply minimum sample sizes and uncertainty bounds before changing rankings.
- Attribute outcomes to capability version, service owner, execution context, and relevant dependency state.
- Retain provenance and explain why a score changed.

**Boundary:** statistics inform prioritization and routing recommendations. They never override capability permissions, risk policy, Council decisions, or human approval.

### 3.5 Council integration — governance layer

**Responsibility:** Ensure strategic recommendations become governed decisions before consequential work is admitted.

**Proposed decision path:**

1. Director forms an evidence-linked advisory recommendation or plan.
2. Deterministic validators check schema, evidence, ownership, dependencies, scope, and capability readiness.
3. Risk and autonomy policy determine the required governance path.
4. Council deliberates where required and records a structured decision with rationale and dissent/uncertainty.
5. DecisionGate verifies authority, workspace scope, policy, and approval requirements.
6. Workforce admission and the canonical Action Kernel admit approved work.
7. Execution receipts and independent outcome verification feed platform_state and capability statistics.

**Invariants:**
- Council recommendation is not execution authority.
- Human approval remains mandatory for sensitive, irreversible, security-sensitive, financial, or production-impacting operations as policy requires.
- No direct Director-to-handler execution shortcut is introduced.
- Every admission and decision is auditable, attributable, and subject to cancellation/rollback controls.
- Rejection, timeout, missing evidence, or governance unavailability fails closed.

## 4. End-to-end operating model

The five capabilities form one governed feedback loop:

1. Subsystems publish typed observations and lifecycle events.
2. platform_state reconciles these events with authoritative subsystem records.
3. Service manifests identify the owner and contract for each service, capability, and event.
4. Director uses the state and ownership map to identify priorities and propose dependency-aware plans.
5. Capability statistics inform readiness and routing without granting authority.
6. Council and DecisionGate govern consequential recommendations.
7. Workforce admission and Action Kernel execute only authorized work.
8. Receipts and independently verified outcomes update the evidence base and capability statistics.
9. Director explains what changed, why it changed, and what remains uncertain.

## 5. Delivery sequence and review gates

### Gate 0 — Contract and dependency audit
- Map existing state stores, event producers/consumers, manifests, capability catalog, Council, DecisionGate, workforce admission, and Action Kernel.
- Identify the canonical owner for each data field and capability.
- Record gaps and duplicate contracts before proposing code.
- **Exit:** reviewed component map and no unresolved ownership conflicts.

### Gate 1 — platform_state contract
- Define the state schema, provenance model, freshness rules, scope rules, and reconciliation contract.
- Map existing sources into the read model without duplicating subsystem-owned records.
- **Exit:** tests cover stale, missing, conflicting, delayed, duplicate, and cross-workspace evidence.

### Gate 2 — service manifest contract
- Define the manifest schema and registry validation.
- Reconcile current topology, routes, capabilities, and runtime bindings with manifest ownership.
- **Exit:** every registered capability and event has one accountable owner; invalid references fail CI/startup safely.

### Gate 3 — typed coordination events
- Define event envelope and versioning/compatibility policy.
- Connect existing bus publishers and consumers incrementally.
- **Exit:** fan-out, idempotency, retry recovery, ordering, correlation, and competing-consumer tests pass.

### Gate 4 — capability statistics
- Define evidence provenance, metric windows, minimum sample sizes, uncertainty, and version attribution.
- Derive statistics only from existing verified receipts and outcomes initially.
- **Exit:** missing/unverified evidence cannot increase reliability scores; score changes are explainable.

### Gate 5 — Council governance convergence
- Map Director recommendations through Council, DecisionGate, workspace authorization, workforce admission, and Action Kernel.
- Remove or block any parallel path that can bypass the canonical authority boundary.
- **Exit:** allowed, rejected, unauthorized, high-risk, and human-approval scenarios are covered end to end.

### Gate 6 — organism-level acceptance
- Test cross-subsystem planning, event delivery, restart/reconciliation, workspace isolation, false success receipts, dependency failure, retries, cancellation, and rollback.
- Demonstrate that Director never triggers content publishing.
- **Exit:** staging evidence proves the complete observe-to-verified-outcome loop; no production deployment is implied.

## 6. Required acceptance evidence

A phase is complete only when its tests and runtime evidence demonstrate:

- One canonical owner per service and capability.
- State facts have source, timestamp, scope, and freshness.
- Unknown or conflicting evidence remains visible rather than being converted into healthy/zero values.
- Event delivery is idempotent and traceable through fan-out and retry.
- Capability reliability reflects verified outcomes, not acknowledgements.
- Workspace isolation holds across state, events, plans, statistics, and governance.
- Council and DecisionGate cannot be bypassed by Director or model output.
- Unapproved plans create no executable tasks and cause no side effects.
- The AI Software Engineering Vertical remains functional and retains its governed execution path.
- Director does not own brand publishing or directly invoke publishing workflows.

## 7. Explicit non-goals

This proposal does not authorize:
- implementing any of the five capabilities;
- changing production or unsuspending production services;
- deploying to staging;
- adding database migrations, new paid infrastructure, or new Supabase cost;
- changing Gateway or Command Center behavior;
- replacing the existing unified bus, Action Kernel, Council, or workforce runtime;
- removing or weakening the AI Software Engineering Vertical;
- granting Director autonomous production execution.

## 8. Decisions requested from reviewers

1. Confirm platform_state is a reconciled read model, not a new system of record.
2. Confirm service manifests are the canonical ownership/contract registry while existing topology and catalogs become validated projections.
3. Confirm the existing unified bus is the coordination transport; only contracts and reliability semantics are standardized here.
4. Confirm capability statistics use independently verified outcomes and cannot grant authority.
5. Confirm Council → DecisionGate → workforce admission → Action Kernel is the sole consequential execution path.
6. Confirm the delivery gates and explicit non-goals before implementation is decomposed into small, testable PRs.

**Recommendation:** approve or amend this architecture contract first. Only after review should Gate 0 be implemented as a read-only audit, followed by separately reviewed implementation increments.
