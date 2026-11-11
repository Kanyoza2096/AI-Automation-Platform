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

### 3.6 Workload identity and cryptographic authority

**Responsibility:** Give each autonomous actor a verifiable non-human identity and bind its authority to a signed, short-lived session credential.

**Director identity:**
- Register Director as a dedicated workload/service principal, for example `svc:kdo:director`, not as a human user account with an `is_species` flag.
- Its initial scope set is deliberately narrow: `director:read` and `director:dispatch`.
- `director:read` permits only the platform-state and evidence projections explicitly exposed to Director.
- `director:dispatch` permits Director to submit a typed, traceable dispatch/proposal request to the governed coordination path. It does **not** permit direct handler execution, arbitrary tool invocation, publishing, approval, or production deployment.
- Tool-specific permissions, workspace scope, environment scope, and risk policy further restrict each request.

**Signed workload token contract:**
- Issue short-lived signed workload/session tokens from a trusted platform identity issuer. Prefer asymmetric signing so services verify with public keys and the private signing key remains isolated from application workloads.
- Bind at least issuer (`iss`), audience (`aud`), subject (`sub`), actor type, agent ID, scopes, platform/workspace scope, session ID (`sid`), issued-at (`iat`), not-before (`nbf`), expiry (`exp`), unique token ID (`jti`), signing key ID (`kid`), and a confirmation/key-binding claim (`cnf`).
- Verify signature, allowed algorithm, issuer, audience, time bounds, actor status, token revocation/rotation policy, and registry binding on every authenticated request.
- Bind the signed token to the workload session key using mutual TLS or proof-of-possession (for example, a DPoP-style per-request signature validated against `cnf`); a copied bearer token alone must not be sufficient for a workload session. Enforce a short maximum lifetime, key rotation, revocation, nonce/replay controls, and fail-closed behavior for unknown keys or malformed claims.
- Effective authority is the intersection of signed token scopes, active Agent Registry grants, service/tool policy, workspace scope, and governance requirements. Registry or database state may revoke or further restrict a signed grant, but must never broaden it; a token claim alone cannot create a new permission.
- Never accept actor type, agent ID, or scopes from request headers, user metadata, mutable profile fields, or unsigned payloads.

**Migration away from `is_species`:**
- `is_species` is currently present in the canonical `AuthIdentity` model and Director authorization paths. It must not remain an independent source of platform authority.
- First inventory every read/write of `is_species`, `species_privileges`, master-token and Gateway-token paths, and every authorization branch that treats them as global access.
- Introduce a typed verified-principal context derived from signed workload claims and explicit human platform grants. Migrate each consumer to named scopes and explicit platform/workspace boundaries.
- During transition, any compatibility projection must be derived only from a verified principal and must not grant broader authority than its scopes. It must not trust or persist a mutable database flag.
- Remove the legacy boolean and broad master/service-token bypass only after all consumers, middleware, background jobs, and tests have migrated. No blanket replacement or lockout-prone cutover.
- Human operator and owner permissions remain distinct from workload identities; a workload principal must never impersonate a human.

**Boundary:** authentication proves who is calling and what signed scopes the issuer granted. Authorization still evaluates registry grants, policy, workspace boundaries, Council/DecisionGate, and the canonical Action Kernel path.

### 3.7 Platform Agent Registry — autonomous actor governance

**Responsibility:** Maintain the authoritative inventory of every autonomous actor that can reason, call tools, dispatch work, or initiate an automated workflow.

This is distinct from the service-manifest registry:
- Service manifests describe software services, their owners, interfaces, and operational contracts.
- Agent Registry entries describe autonomous principals, their accountable team, allowed tools, scopes, and lifecycle.
- An actor may run within a service, but service ownership does not implicitly grant that actor the service's permissions.

Each actor record should include:
- Stable `agent_id`, principal subject, display name, actor class, purpose, version, and lifecycle state (proposed, active, suspended, retired).
- Owning team and accountable human/service owner.
- Runtime/service association and permitted environments.
- Explicit allowlisted tool IDs and tool versions.
- Maximum scopes, workspace/platform boundaries, data classifications, and delegation constraints.
- Risk class, autonomy level, required Council/DecisionGate path, human-approval conditions, and budget/operational limits where applicable.
- Credential issuer/key binding and token policy reference; never store plaintext credentials or private signing keys in the registry.
- Created/updated provenance, reviewer/approver, and audit history.

Initial inventory must explicitly include Director, Council actors, Content Engine/brand scheduler actors, Guardian, and the AI Software Engineering Vertical's planner, orchestrator, specialist agents, and worker identities where they can act autonomously. The inventory must be derived from repository/runtime discovery and owner confirmation; this list is a minimum, not a claim that it is exhaustive.

**Enforcement rules:**
- Unknown, unregistered, suspended, retired, or ownerless actors are denied tool execution and dispatch by default.
- Runtime token identity must match the registered principal and active actor record.
- Effective tools and scopes are the intersection of the registry allowlist, signed token claims, capability permissions, and governance policy.
- Tool grants are explicit and versioned; registering an actor does not authorize every tool available to its runtime.
- Delegation preserves the original actor, records the delegating actor and target, and cannot widen scopes or cross workspace boundaries.
- Council is registered as a governance actor with its own narrow deliberation/decision tools; it does not inherit Director's dispatch identity.
- Content Engine and brand schedulers retain their own publishing ownership and scoped authority. Director cannot borrow or impersonate those credentials.
- Registry changes are themselves governed, audited, and subject to review; emergency suspension must take effect promptly.

**Reuse first:** inspect and reconcile `services/v71_1_agent_identity.py`, `services/v71_2_agent_capability_permissions.py`, `services/advanced_agent_control_plane.py`, the existing engineering Agent Registry, and current identity/session hardening. Extend the existing canonical identity/permission components or compose them behind one platform registry; do not create a competing in-memory-only registry.

### 3.8 Structured Director tool-call tracing

**Responsibility:** Make every Director tool invocation diagnosable from the originating objective/run through the exact tool request, authorization decision, result, and failure.

Reuse the existing Phase 9.2 correlation contract and OpenTelemetry integration. Each invocation should create a child span and structured invocation record containing:
- `run_id`, `invocation_id`, `agent_id`, `tool_id`, tool version, and owning service;
- `trace_id`, `span_id`, `correlation_id`, and parent/causation identity;
- authenticated principal subject, effective scope names, workspace/platform scope, and environment;
- start/end timestamps, duration, attempt/retry number, and terminal status;
- validated argument schema/version, safe structured argument values, redaction metadata, and an argument digest;
- policy/registry check result and governance decision reference before invocation;
- bounded result summary or result digest, without dumping sensitive payloads;
- error class/code, sanitized message, retryability, and a protected diagnostic reference for stack details where appropriate.

**Argument and secret safety:**
- Record exact semantic arguments only after schema validation and redaction. Preserve safe identifiers and values needed to reproduce the failure, but redact tokens, API keys, passwords, authorization headers, private keys, personal data, and other classified fields.
- Never record bearer tokens, raw credentials, signing material, or unrestricted request/response bodies.
- Apply access controls and retention policy to traces; privileged stack traces and sensitive diagnostic payloads must not be exposed in ordinary Director reports.
- A digest of canonicalized arguments can support integrity and comparison, but it does not replace the safe argument record needed for diagnosis.

**Failure behavior:**
- A tool exception, timeout, authorization denial, schema error, retry, or cancellation must produce a terminal invocation outcome with the corresponding reason and parent run correlation.
- Do not collapse tool failures into only `task_failed`. The task event should reference the invocation IDs and summarize the failure chain.
- Preserve the original exception classification and sanitized diagnostics; never turn a failed tool into a successful empty result.
- Trace creation failure must not bypass authorization or cause a tool to execute without its required policy checks. For consequential tools, inability to establish required audit evidence should fail closed.

**Example:** if `run_guardian_scan` fails, an operator should be able to follow the Director run → tool invocation span → exact validated/redacted repository and scan options → authorization decision → Guardian service result/exception → retry or terminal failure, all under one correlation ID.

**Guardian-specific authority separation:**
- The current Director catalogue labels `run_guardian_scan` as `Authority.AUTO` / `Risk.LOW`, while its bound `GuardianService.scan_all()` flow can invoke configured auto-fix behavior and create GitHub PRs.
- Split the capability into `guardian:scan` (strictly read-only discovery/reporting) and a separate remediation capability such as `guardian:remediate`.
- The scan implementation must not invoke auto-fix, even if a global Guardian auto-fix setting is enabled.
- Remediation requires its own registered tool grant, higher risk classification, Council/DecisionGate review, and human approval where policy requires. Director's initial `director:dispatch` scope does not include remediation authority.
- Acceptance tests must prove scan-only runs cannot modify repositories, create PRs, or trigger remediation side effects.

**Boundary:** tracing is observability, not authority. Trace data cannot authorize a tool or alter a governance decision.

## 4. End-to-end operating model

The five strategic capabilities operate with a cross-cutting identity, agent-governance, and observability plane:

1. Subsystems publish typed observations and lifecycle events.
2. platform_state reconciles these events with authoritative subsystem records.
3. Service manifests identify the owner and contract for each service, capability, and event.
4. Director uses the state and ownership map to identify priorities and propose dependency-aware plans.
5. Capability statistics inform readiness and routing without granting authority.
6. Council and DecisionGate govern consequential recommendations.
7. Workforce admission and Action Kernel execute only authorized work.
8. Receipts and independently verified outcomes update the evidence base and capability statistics.
9. Each autonomous actor authenticates as its registered non-human principal with short-lived signed scopes; unknown actors are denied.
10. Director tool calls emit correlated, redacted invocation traces linked to decisions, governance checks, results, and errors.
11. Director explains what changed, why it changed, and what remains uncertain.

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

### Gate 5 — Workload identity, Agent Registry, and tool-call observability
- Define the signed workload-token claims, issuer/verifier/key-rotation contract, scope vocabulary, revocation, and replay controls.
- Reconcile existing agent identity and capability permission registries into a platform-wide actor inventory with accountable owners and explicit tool allowlists.
- Migrate Director and other autonomous actors away from mutable `is_species` authorization, using a compatibility bridge only while all call sites are migrated.
- Instrument Director tool calls using Phase 9.2 correlation context and OpenTelemetry spans, with safe argument capture, redaction, authorization decisions, results, and errors.
- **Exit:** forged/expired/revoked tokens, unknown agents, scope escalation, delegation widening, workspace crossing, secret leakage, trace gaps, and tool failure attribution tests all pass.

### Gate 6 — Council governance convergence
- Map Director recommendations through Council, DecisionGate, workspace authorization, workforce admission, and Action Kernel.
- Remove or block any parallel path that can bypass the canonical authority boundary.
- **Exit:** allowed, rejected, unauthorized, high-risk, and human-approval scenarios are covered end to end.

### Gate 7 — organism-level acceptance
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
- Every autonomous actor is registered, owned, scope-bound, and denied by default when unknown or suspended.
- Signed workload scopes are verified and intersected with registry/tool grants; no mutable `is_species` flag grants platform authority.
- Director tool invocations have correlated, redacted traces with sufficient validated arguments and error evidence to diagnose failures without leaking secrets.
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
- granting Director autonomous production execution;
- treating `director:dispatch` as permission to execute tools directly;
- logging raw credentials or unrestricted tool arguments/results;
- replacing existing agent identity, session, event, or trace infrastructure before the Gate 0 audit proves a gap.

## 8. Decisions requested from reviewers

1. Confirm platform_state is a reconciled read model, not a new system of record.
2. Confirm service manifests are the canonical ownership/contract registry while existing topology and catalogs become validated projections.
3. Confirm the existing unified bus is the coordination transport; only contracts and reliability semantics are standardized here.
4. Confirm capability statistics use independently verified outcomes and cannot grant authority.
5. Confirm Council → DecisionGate → workforce admission → Action Kernel is the sole consequential execution path.
6. Confirm the delivery gates and explicit non-goals before implementation is decomposed into small, testable PRs.
7. Confirm Director is a non-human principal with only `director:read` and `director:dispatch` initially, with dispatch limited to governed admission.
8. Confirm signed workload tokens and the Agent Registry—not mutable `is_species` state—are the authority source for autonomous actors.
9. Confirm the Agent Registry covers Director, Council, Content Engine/brand schedulers, Guardian, and AI Software Engineering actors, with explicit owner teams and tool allowlists.
10. Confirm all Director tool invocations require correlated structured traces with redacted argument capture and actionable error attribution.

**Recommendation:** approve or amend this architecture contract first. Only after review should Gate 0 be implemented as a read-only audit, followed by separately reviewed implementation increments.
