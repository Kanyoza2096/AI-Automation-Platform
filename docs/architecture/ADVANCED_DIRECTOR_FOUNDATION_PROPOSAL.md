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

### 3.9 Versioned authorization scope catalogue

**Status:** Proposed vocabulary and semantics for review; not active grants or runtime configuration.

**Catalogue version:** kdo-authz-scopes/v1. Every scope definition is immutable once referenced by a signed token or authorization decision. A semantic change requires a new catalogue version and, where meaning or boundary changes, a new scope identifier. Renaming or widening an existing scope in place is prohibited.

**Scope grammar:** `&lt;domain&gt;:&lt;resource&gt;:&lt;action&gt;` for resource-specific permissions; narrowly defined actor-level scopes may use `&lt;actor&gt;:&lt;action&gt;`. Scope strings are exact identifiers, not glob patterns. Wildcards (including *, admin, owner, and implicit superuser aliases) are not valid workload grants.

Each catalogue entry must define: scope ID; human-readable purpose; allowed resource types and action verbs; explicit exclusions; allowed actor classes; owning team; maximum risk tier; required workspace/environment constraints; required tool/capability IDs and versions; delegation policy; required policy and governance checks; approval class; audit event; and catalogue version. An authorization request must supply concrete resource IDs and environment/workspace context; a scope alone never implies unrestricted resource access.

#### Initial scope definitions

| Scope | Permitted action and resource boundary | Eligible principal | Owner | Maximum risk / governance |
|---|---|---|---|---|
| director:read | Read only approved, redacted platform-state and evidence projections explicitly exposed to Director; no raw secret or unrestricted tenant data | Director | Platform Intelligence | Low; policy-filtered read and audit |
| director:dispatch | Submit a typed proposal/dispatch envelope to governed admission; cannot invoke handlers, create an approved execution, approve, publish, remediate, mutate registry, or deploy | Director | Platform Intelligence | Medium; schema, ownership, scope and risk validation; Council/DecisionGate as required |
| platform:state:read | Read explicitly named platform-state projections | Approved platform observers | Platform Core | Low; projection-level authorization |
| platform:manifest:read | Read service manifest metadata | Approved platform observers | Platform Architecture | Low; sensitive fields filtered |
| platform:manifest:manage | Propose or perform reviewed manifest lifecycle changes | Designated platform maintainers; machine actors only through governed proposal tools | Platform Architecture | High; reviewed change and audit |
| platform:events:publish | Publish only allowlisted event types and schema versions, for the registered source identity and workspace | Named event-producing principals, including narrowly scoped bridge identities | Event Platform | Medium; event-type allowlist, schema validation, idempotency and audit |
| platform:events:consume | Subscribe to named event types/channels and workspace partitions | Registered consumers | Event Platform | Low/medium; per-channel and per-workspace filters |
| platform:observability:read | Read authorized health, metrics, traces and diagnostic summaries; sensitive payloads remain separately protected | Director, Guardian scanner, approved operators | Reliability | Low; redaction and data-classification filters |
| platform:operations:operate | Execute explicitly listed operational actions on named non-production resources | Designated operators or approved operational actors | Platform Operations | High; DecisionGate and human approval according to action |
| workspace:resource:read | Read named resource classes inside the token's bound workspace | Human members and explicitly registered workload actors | Workspace Platform | Low; workspace membership and resource policy |
| workspace:resource:write | Write named resource classes inside the bound workspace | Explicitly authorized human or workload actors | Workspace Platform | Medium/high; resource policy, audit and approval where consequential |
| council:deliberate | Read the decision packet and submit deliberation/recommendation records; no execution | Council actors | Governance | Medium; decision record and provenance |
| council:decide | Record a Council decision only within assigned quorum, policy and decision domain; does not itself execute | Authorized Council actors | Governance | High; quorum, conflict-of-interest, evidence and immutable decision audit |
| guardian:scan | Read-only repository discovery and security scanning; no write, fix, branch, PR, notification-triggered remediation, or deployment side effect | Guardian scanner | Platform Security | Low/medium; read-only tool allowlist and invocation trace |
| guardian:remediate | Execute a specifically approved remediation plan against named repositories/resources | Separate Guardian remediation principal | Platform Security | High/critical; Council/DecisionGate and human approval as required |
| content:generate | Generate content only for explicitly assigned brand/workspace and approved campaign inputs | Content Engine principal | Content Platform | Medium; brand policy and content review |
| content:publish | Publish approved content to the assigned brand/channel under scheduler policy | Brand Scheduler principal only | Brand Operations | High; schedule, brand authorization, approval and publication audit |
| engineering:plan | Produce implementation plans and task decomposition within the authorized engineering workspace | Engineering planner/orchestrator | AI Software Engineering | Medium; task policy and workspace boundary |
| engineering:code:read | Read source and project artifacts in assigned engineering workspace | Named engineering agents/workers | AI Software Engineering | Low; repository/workspace boundary |
| engineering:code:write | Modify files only in the assigned isolated workspace/branch; no merge or deployment implied | Named engineering specialists/workers | AI Software Engineering | High; plan approval, workspace isolation, tests and review |
| engineering:execute | Run allowlisted commands/tests in an isolated, resource-bounded workspace | Engineering worker principal | AI Software Engineering | High; command policy, sandbox and execution receipt |
| engineering:merge | Request or perform a reviewed merge only under repository branch protection and approval policy | Designated human maintainers; automation may submit a merge request, not bypass protection | Engineering Governance | Critical; independent review and required human authorization |
| engineering:deploy:staging | Request or perform an approved staging deployment for explicitly named service/version | Designated release principal | Release Engineering | High; release gate, tests, authorization and rollback evidence |
| engineering:deploy:production | Request a production release; execution requires separate explicit human release authorization and platform release controls | Human release authority; machine principal may only prepare/request | Release Engineering | Critical; explicit human authorization, independent checks and rollback plan |
| identity:registry:read | Read redacted principal, grant and lifecycle metadata | Approved identity administrators/auditors | Identity & Security | Low; secrets never exposed |
| identity:registry:manage | Propose or perform principal lifecycle, grant, key-binding and scope changes | Human identity administrators only | Identity & Security | Critical; dual control, step-up auth, immutable audit |
| identity:credential:revoke | Revoke a specific credential/session/key binding | Human identity administrators and narrowly designated emergency security principal | Identity & Security | Critical; immediate effect, reason and immutable audit |

This table is the initial vocabulary, not a claim that every listed scope is already implemented or that every actor should receive one. Before enforcement, each row must be expanded into machine-readable resource/action constraints and reconciled against the route/tool inventory. Domain-specific scopes must be added only with an accountable owner and a concrete consumer.

#### Principal assignment and separation rules

- Director receives exactly director:read and director:dispatch initially. No inherited platform:*, workspace:*, or service-owner authority.
- Human platform super-admin is a human role backed by separate human authentication and explicit grants. It is never a workload scope and must never be copied into a machine token.
- Council's deliberation and decision identities are distinct from Director. A Council decision is not an execution credential; admission still checks DecisionGate and Action Kernel.
- Guardian scan and remediation use separate principals or cryptographically distinct workload identities. A scanner token cannot be upgraded by a mutable registry flag.
- Content Engine generation and Brand Scheduler publishing remain distinct. Only the scheduler's explicitly bound principal can request publication, subject to brand and approval policy. Director has no content scopes.
- Engineering planner, specialist, and worker identities are separate. Planning does not imply source-write; source-write does not imply command execution; execution does not imply merge or deployment.
- KCPG bridge identities receive only platform:events:publish for an explicit event-type allowlist, source identity and workspace boundary. They receive no general platform read/write permission.
- Socket access is authorized per channel and event direction; a successful socket handshake does not imply permission to subscribe, publish, or invoke every event.
- No service principal inherits all permissions of its hosting service. Delegation preserves the original subject and cannot add scopes, expand resource sets, change environment, or cross workspace boundaries.

#### Risk tiers and mandatory controls

| Tier | Typical actions | Minimum controls |
|---|---|---|
| R0 — Read | Redacted state, manifests, approved metrics, repository scan | Exact scope + resource/workspace/environment filter; audit access |
| R1 — Bounded proposal | Director dispatch, engineering plan, event publication, Council deliberation | R0 controls + schema validation, idempotency, owner check, policy evaluation and trace |
| R2 — Reversible operation | Isolated workspace writes, bounded non-production operations | R1 controls + explicit tool allowlist, approved task/plan, sandbox or target constraints, execution receipt and rollback |
| R3 — Consequential operation | Guardian remediation, content publication, merge, staging release, sensitive platform operation | R2 controls + DecisionGate/Council where applicable, independent review, human approval where policy requires, before/after evidence and rollback |
| R4 — Critical authority | Production deployment, identity/grant/key changes, credential issuance or broad revocation | Explicit human authorization, step-up authentication, dual control, separation of duties, immutable audit, bounded execution window and verified rollback/recovery |

Risk is assigned to the concrete operation and target, not merely to the actor or tool label. A low-risk actor invoking a high-risk tool remains subject to the tool's higher tier. A Council decision can satisfy a governance prerequisite but cannot bypass human approval or grant a token scope.

#### Authorization evaluation order

Every request must be denied unless all checks succeed, in this order:

1. Verify token signature, allowed algorithm, issuer, audience, time bounds, key ID, session binding/proof-of-possession, nonce/replay policy and revocation state.
2. Resolve the stable principal subject and confirm actor type, agent ID, lifecycle status, owner and runtime binding against the Agent Registry.
3. Confirm the exact scope exists in the pinned catalogue version and is assigned to that principal; reject unknown scopes and wildcard syntax.
4. Intersect signed token scopes with registry maximum grants, explicit tool/capability allowlist, and any narrower per-session grant. Registry state may revoke or narrow; it may not expand the signed token.
5. Match the requested action to the exact resource type, resource ID, workspace, environment, event type/channel, repository, brand or deployment target constraints. Missing or ambiguous boundaries deny.
6. Evaluate risk tier, policy, delegation chain, budget, separation-of-duties, Council/DecisionGate and human approval requirements.
7. Establish the required audit/trace context before any consequential side effect. If mandatory evidence cannot be recorded, fail closed.
8. Admit through the canonical governed execution path. A scope check alone is never a direct handler-execution shortcut.

**Deny-by-default cases:** unknown or retired scope version; unknown principal/tool/resource; missing owner; suspended actor; expired or revoked token; invalid proof-of-possession; workspace/environment mismatch; missing event/channel allowlist; delegation widening; stale or conflicting approval; policy/governance unavailable; or required audit evidence unavailable.

#### Versioning, migration and revocation

- Tokens carry a pinned scope-catalogue version (scp_ver) and issuer/key identifier (kid). Verifiers reject unsupported versions rather than guessing semantics.
- New scopes begin unassigned. Grant changes require review by the owning team and Identity & Security; R3/R4 grants require independent approval and documented expiry/review date.
- Scope narrowing or revocation takes effect through registry/session revocation checks; scope broadening requires re-issuance of a new signed token after approval. A mutable database row cannot broaden an already-issued token.
- Deprecation uses an overlap window with telemetry proving no active consumers; old scope IDs are denied after the announced cutoff. No silent aliasing to a broader permission.
- Emergency revocation supports principal, session, token ID, signing key and scope-grant revocation. Revocation events are correlated, reasoned and auditable.
- Compatibility with is_species is temporary and must be computed from verified identity plus explicit named grants. It must not map to a wildcard or super-admin scope.
- The catalogue and policy engine must be tested against positive, negative, boundary, replay, delegation, cross-workspace, cross-environment, expired-grant, revoked-key, and governance-unavailable cases before any enforcement rollout.

### 3.10 Signed workload-token claim and proof-of-possession contract

**Status:** Proposed protocol contract for review. This section does not implement an issuer, verifier, key store, token, or active grant.

**Protocol profile:** KDO Workload Token Profile v1, using a signed JWT access token with an explicitly allowlisted asymmetric algorithm. The initial implementation should use a vetted JOSE library and platform key service; no custom cryptography, shared application signing secret, or algorithm negotiation is permitted. The exact algorithm and key-service integration are implementation decisions to be confirmed against supported platform libraries and key custody.

#### Protected header

| Field | Requirement |
|---|---|
| typ | Exact token type kdo-workload+jwt; reject other or missing types |
| alg | One configured asymmetric signing algorithm; reject none, symmetric algorithms and algorithm substitution |
| kid | Required key identifier resolving to an active trusted issuer key |
| crit | Reject unsupported critical extensions; no unrecognized critical semantics |

The verifier selects trusted keys from configured issuer metadata/key distribution, not from arbitrary token-supplied URLs. Key material and issuer trust configuration are provisioned out of band. A token cannot nominate its own trusted issuer or key source.

#### Required claims

| Claim | Contract |
|---|---|
| iss | Exact trusted workload-identity issuer identifier |
| sub | Stable, immutable principal subject for this registered workload identity; never a display name |
| aud | Exact intended API/service audience; use a narrow audience, not a generic platform-wide audience |
| iat, nbf, exp | Issuance, not-before and expiry times; verifier applies bounded clock skew and rejects future-issued, not-yet-valid or expired tokens |
| jti | Cryptographically random, unique token identifier for replay/revocation tracking |
| sid | Unique workload session identifier, bound to the registered runtime session |
| agent_id | Stable Agent Registry identifier; must map to sub and an active registry record |
| actor_type | Controlled enum such as agent, scheduler, worker, service, or human; workload profile rejects human |
| scp | Exact scope identifiers from the pinned catalogue version; no wildcard, prefix or implied inheritance |
| scp_ver | Exact authorization scope catalogue version, initially kdo-authz-scopes/v1 |
| env | Explicit environment identifier such as staging; token environment must match the receiving service and registered runtime |
| cnf | Confirmation/key-binding claim for proof-of-possession; required, never optional for workload tokens |
| authz_ver | Version of the authorization-policy contract used at issuance, distinct from the scope catalogue version |

The issuer may include narrowly defined, versioned extensions only after review. No claim may carry secrets, raw credentials, private data, or arbitrary user-controlled authorization values. Do not use email, display name, request headers, mutable profile metadata, or client-provided actor labels as identity evidence.

#### Conditional claims and bounded context

- wsp (workspace binding): required for workspace-bound actors and requests. It identifies one workspace or an explicitly registered set of workspace IDs; an omitted workspace claim means no workspace access, not global access.
- tool_allow: optional, explicit tool/capability IDs and versions for a workload session. It may narrow the registry allowlist, never expand it.
- res (resource constraints): optional references to versioned registry constraints or exact resource IDs. It is not a wildcard expression language.
- act (delegation actor): required only for an approved delegated token. It records the immediate delegating principal and a bounded delegation reference. The original subject and full delegation chain remain auditable. Delegation never changes the original actor's accountability.
- budget or execution limits: if included, values can only narrow registry and policy limits; absence does not mean unlimited budget.

A token for a platform-scoped observer may have no wsp claim but must carry a registry-defined platform read boundary and narrow audience. Platform scope is never inferred from an empty workspace claim.

#### Proof-of-possession (cnf) and request binding

The initial profile must support one reviewed binding mechanism, selected during implementation:
- Mutual TLS certificate binding using the standardized certificate thumbprint confirmation claim; or
- A workload-held asymmetric key bound by a confirmation JWK thumbprint, with a per-request signature protocol that binds the HTTP method, canonical target URI, token hash, body digest where applicable, timestamp, nonce and unique proof identifier.

Do not accept a copied bearer token as sufficient proof for a workload identity. Do not accept a client-provided public key unless its thumbprint matches the signed cnf claim and the key is proven possessed. Canonicalization rules must be fixed and tested to prevent URI, proxy, query-string or body-hash ambiguity. Nonces and proof identifiers must be checked for replay within a bounded acceptance window.

The chosen mechanism must integrate with the actual Render/network topology and trusted proxy boundary. Do not assume mTLS is end-to-end merely because an upstream proxy terminates TLS. If the selected deployment path cannot preserve verifiable workload proof, fail closed rather than silently downgrade to bearer-only authentication.

#### Lifetime, renewal and revocation

- Workload access tokens should be short-lived, with a proposed maximum lifetime of 10 minutes. The issuer may issue shorter tokens; longer lifetimes require a separately reviewed exception and must not be a default.
- Workload sessions do not receive long-lived refresh tokens. The workload re-authenticates to the trusted issuer using its registered runtime identity/key binding to obtain a new access token.
- Verifiers enforce iat, nbf, exp, bounded clock skew, audience and issuer exactly. Expiry is not extended by local clock drift or registry cache state.
- Revocation must support token jti, session sid, principal sub/agent_id, key kid and individual grant. Emergency principal/session/key revocation must be propagated to every verifier within a defined, measured revocation objective.
- If a verifier cannot establish current revocation state within the approved freshness bound, high-risk and consequential operations fail closed. Low-risk cached-read behavior, if any, must be separately specified and cannot be generalized to writes or dispatch.
- Key rotation uses overlapping trusted public keys: publish/activate new key, issue with new kid, observe adoption, then retire old key after all tokens expire and rollback windows close. Compromised keys are revoked immediately; overlap is not allowed to preserve a compromised key.
- Token and proof material must never be written to logs, traces, task records, exception messages or Director reports.

#### Issuance and verification sequence

1. A registered workload proves its runtime identity to the trusted issuer through the approved platform workload-identity mechanism; the issuer does not trust caller-supplied agent_id or scope lists.
2. The issuer resolves the active Agent Registry record, runtime binding, accountable owner, environment, permitted workspace, current grant and approved tool allowlist.
3. The issuer intersects requested scopes with registry maximum grants, approved session grants, scope-catalogue semantics and policy. It denies unknown, expired, ownerless, suspended or unreviewed principals.
4. The issuer signs a short-lived token bound to the workload-held key/session and exact service audience. It records an issuance audit event without recording the token itself.
5. The receiving service validates signature, trusted iss/kid, typ, aud, time claims, jti, sid, agent_id/sub binding, scp_ver, authz_ver, cnf, proof freshness and revocation.
6. The service resolves current registry and policy state and computes effective authority as the intersection of token scopes, registry grant, tool allowlist, resource/workspace/environment constraints and governance decisions.
7. The service authorizes the exact action and target, establishes required trace/audit evidence, then admits consequential work through the canonical Council/DecisionGate → workforce admission → Action Kernel path.
8. The service emits a correlated authorization outcome. Denials are explicit and do not fall through to legacy shared-token or is_species bypasses.

#### Required negative tests

Reject wrong issuer/audience/algorithm/type; missing or unknown kid; forged signature; malformed or duplicate claims; future iat; invalid nbf; expired token; excessive lifetime; reused jti or proof nonce; missing/invalid cnf; wrong proof key; wrong HTTP method/URI/body digest; wrong sid; sub/agent_id mismatch; unsupported scp_ver/authz_ver; unknown scope; registry suspension; stale revocation state for consequential action; wrong environment/workspace; unregistered runtime; and attempted scope/resource/delegation broadening.

### 3.11 Platform Agent Registry logical schema and lifecycle

**Status:** Logical data contract only. It is not a database migration or a mandate to create a second registry implementation. Gate 0 must first reconcile the existing V71 identity/capability registries and engineering registry, then select the canonical storage and API owner.

The Agent Registry is the source of truth for workload-principal identity, ownership, lifecycle and maximum authority. It is not the service-manifest registry, not the token issuer, not a secret store, and not the policy engine. Service manifests describe software services; Agent Registry records describe principals that can act.

#### Core principal record

| Field | Type / contract | Required invariant |
|---|---|---|
| agent_id | Stable opaque identifier, primary logical key | Immutable; never reused after retirement |
| principal_subject | Unique issuer subject (sub) | One-to-one binding to the workload principal; uniqueness enforced |
| display_name | Human-readable label | Non-authoritative; may change |
| actor_type | Controlled enum: agent, scheduler, worker, service | Human identities use the separate human identity system |
| purpose | Bounded description and approved use | Explicit, reviewable and owned |
| identity_version | Version of principal contract | Changes are auditable |
| lifecycle_state | proposed, active, suspended, retired | Only active principals can obtain new workload tokens |
| owner_team_id | Canonical team reference | Required; must resolve to an active owner |
| accountable_owner_id | Human owner reference in human identity system | Required; no self-ownership by an agent |
| service_manifest_id | Optional owning runtime/service reference | Must resolve when a service association exists |
| runtime_binding_id | Reference to an approved runtime binding | Required before activation |
| allowed_environments | Explicit environment set | No implicit wildcard; production requires separate review |
| risk_ceiling | Maximum permitted risk tier | Cannot override a tool's higher risk |
| autonomy_class | Controlled policy enum | Must align with approval and supervision rules |
| created_at, updated_at | UTC timestamps | System generated |
| created_by, reviewed_by, approved_by | Human/system provenance references | High-risk grants require independent reviewer |
| review_due_at | UTC review deadline | Active grants require periodic recertification |
| suspension_reason, retired_at | Nullable lifecycle evidence | Required when applicable |

#### Related records (logical relations)

1. **Scope grants** — grant ID, agent ID, exact scope ID and catalogue version, maximum resource constraints, workspace/environment constraints, grant status, effective-from/expiry, owner approval, reviewer, reason, provenance and revocation timestamp. Grants can narrow but never exceed the scope set signed into a token.
2. **Tool bindings** — binding ID, agent ID, exact tool/capability ID and version, allowed action set, resource constraints, maximum risk, required policy/governance route, status, expiry and approving owner. A tool absent from this list is unavailable even if the runtime can technically import or reach it.
3. **Runtime bindings** — binding ID, agent ID, runtime/service identity, deployment/environment, workload identity provider reference, key-binding reference or attested runtime identity, session policy and lifecycle. Do not store private keys, bearer tokens or secret values.
4. **Workspace/resource bindings** — binding ID, agent ID, exact workspace/resource IDs and resource classes, permitted actions, environment, data classification, expiry and owner approval. No workspace is inferred from a request header.
5. **Delegation records** — delegation ID, source and target principal IDs, original subject, allowed exact scopes/tools/resources, purpose, expiry, depth limit, approval and revocation. Delegation cannot cross a boundary not held by the original principal.
6. **Approval records** — immutable decision reference, policy version, approver identity, quorum/independence evidence, risk classification, approved scope/resource/action, validity window and revocation/supersession reference.
7. **Credential/key bindings** — issuer, kid, public-key thumbprint/certificate reference, status, activation/retirement times and rotation/revocation events. Private signing and workload keys remain in approved key custody, never in registry rows.
8. **Audit events** — append-only actor lifecycle, grant, tool binding, runtime binding, owner, key, suspension, delegation and approval changes with before/after digests, reason, correlation ID, actor and timestamp. No secret or token material.

Relations should be normalized or otherwise enforce uniqueness and referential integrity. Exact physical tables, indexes, constraints, retention and migrations are deferred until the canonical existing registry has been assessed. Do not add a new paid database or duplicate source of truth to implement this logical contract.

#### Lifecycle and governance transitions

- Proposed → active: requires a resolved accountable owner, runtime binding, purpose, environment boundary, explicit tool/scope grants, review evidence, passing identity/security tests and approved activation. No default grants.
- Active → suspended: immediately blocks new token issuance and consequential authorization; revokes or invalidates active sessions according to the revocation objective. Suspension is available to the designated emergency security authority and must record reason/evidence.
- Suspended → active: requires remediation evidence, owner confirmation, independent review and re-approval of grants; prior tokens/sessions are not silently revived.
- Active/suspended → retired: terminal identity state. Reuse requires a new principal ID and fresh review; historical audit remains intact.
- Grant or tool-binding changes are separate governed events. They do not silently mutate the authority of already issued tokens. Narrowing/revocation can take effect immediately through verifier policy; broadening requires approval and new token issuance.
- Owner changes, production-environment access, identity/key changes, R3/R4 grants and delegation are high-risk registry operations requiring independent approval and immutable audit. Identity registry management remains human-only except a separately reviewed emergency revocation mechanism.
- Registry unavailability, referential-integrity failure, missing owner, ambiguous runtime binding or stale critical approval denies activation and consequential work.

#### Initial actor registration plan

| Principal family | Registration boundary | Initial grant posture |
|---|---|---|
| Director | Dedicated Director principal and runtime binding | Exactly director:read, director:dispatch; governed admission only |
| Council | Distinct governance actor identities and quorum roles | Deliberation/decision scopes only; decision does not execute |
| Guardian scanner | Read-only scan identity | guardian:scan; no remediation tool binding |
| Guardian remediation | Separate identity and runtime/tool binding | No grant until approved remediation workflow exists |
| Content Engine | Content generation principal | Generation only for assigned brand/workspace |
| Brand Scheduler | Per-scheduler or explicitly isolated brand principal | Publishing only for assigned brand/channel under approval policy |
| KCPG event bridge | Narrow bridge principal | Allowlisted event publication only; exact source/workspace/type |
| Engineering planner/orchestrator | Separate engineering planning principal(s) | Planning and task dispatch only within approved engineering workspace |
| Engineering specialist | Role-bound specialist identity | Explicit repository/workspace read or write tools as assigned |
| Engineering worker | Isolated execution identity | Approved task, sandbox and command set only; no merge/deploy by implication |
| Meta Engine/autonomous loops | Register only components proven to initiate autonomous actions | Initially unassigned; classify service vs principal before any grant |
| Human operators/owners | Existing human identity and authorization system | Never represented as workload actor or granted workload token |

This is a candidate family list, not proof every component is an independent principal. Gate 0 must confirm runtime boundaries, owner teams, deployment topology and whether each component can initiate actions. A passive library/service does not need an agent principal merely because it exists in the source tree.

#### Registry invariants and operational acceptance

- Exactly one canonical principal subject per active workload identity; no shared sub across unrelated actors.
- Every active principal has one accountable owner, a known runtime binding and explicit allowed environments.
- Every grant is exact, versioned, bounded, time-limited where appropriate and traceable to an approval.
- Every tool binding identifies tool version, action/resource boundary, risk and governance route.
- Registry records contain references and public metadata only, never secrets/private keys/tokens.
- Suspension and revocation are tested across API, socket, event, worker and scheduler entry points.
- A stale cache cannot preserve a revoked high-risk grant beyond the approved revocation objective.
- Actor identity and effective grants remain consistent across request context, event envelopes, worker jobs and delegated calls.
- Registry outage or unknown actor fails closed for activation and consequential actions; no fallback to master/Python shared credentials or is_species.
- All lifecycle and grant changes are independently auditable; an agent cannot approve or widen its own authority.

### 3.12 Existing identity infrastructure convergence and canonical ownership

**Status:** Source-derived convergence assessment from the inspected staging branch. This is an architecture recommendation, not an implementation decision or claim that every runtime consumer has been inventoried.

The target is one coherent workload-principal authority model that composes existing KDO components. It is not a new parallel identity stack. Existing components retain their distinct responsibilities unless a later, reviewed migration proves a specific gap.

#### Existing component responsibility and reuse decision

| Existing component | What the inspected implementation provides | Reuse decision and boundary |
|---|---|---|
| V71.1 Agent Identity Registry | Workspace-scoped agent identity, declared capabilities, lifecycle transitions, deterministic SHA-256 record fingerprint and integrity checks; identity explicitly grants no authorization or execution | Reuse as the agent/workforce identity domain model and lifecycle foundation. Extend through a reviewed adapter/schema for platform principal subject, accountable owner and runtime bindings; do not mistake its fingerprint for a signature or credential |
| V71.2 Agent Capability Permission Registry | Workspace-scoped allow/deny capability grants, exact declared-capability check, explicit-deny precedence, fingerprint validation and scope matching | Reuse as a capability-policy input and compatibility source. Do not equate capability names or legacy scope dictionaries with signed authorization scopes; translate only through an explicit reviewed mapping |
| V71.3–V71.11 workforce registries | Memory, planning, evaluation, budgets, takeover, coordination, bounded operations, runtime and observability built around V71.1/V71.2 | Preserve these registries and their contracts. Agent identity convergence must not replace workforce planning, evaluation, budget, takeover, coordination or execution governance |
| V71.13 Production Runtime Convergence | Composes one workspace runtime and creates V71.1/V71.2 registries, then attaches workforce runtime/control; production path fails closed if durable hydration cannot be established | Reuse as the composition boundary. Avoid a second global runtime or independent registry initialization path |
| V71.18 Workforce Control Plane Persistence | Rehydrates agent identities, capability permissions and workforce state from workspace-filtered Supabase tables, verifies record fingerprints, and reports restart safety | Reuse the existing durable workforce persistence and hydration path. Preserve fail-closed restart behavior. Verify write-through, transactionality, uniqueness, schema constraints, migrations and concurrent update semantics before treating it as a fully governed principal registry |
| Advanced Agent Control Plane | Read-only composition of identity, permissions, planning, evaluation, budgets, takeover, coordination and runtime into a fingerprinted snapshot; authority and execution flags remain false | Reuse as the read-only operator/reasoning projection. It is not an identity issuer, grant authority, token verifier or execution control |
| V83 Identity Access Hardening | Tenant/workspace-bound principal and session records, expiry/state validation and integrity fingerprints; SQLite implementation defaults to :memory: unless a path is supplied | Reuse its identity/session assurance concepts and tests where applicable. Do not create a second principal inventory. Its default storage is process-local memory; production persistence and shared verifier behavior require explicit configuration/evidence |
| V84 Canonical Authorization Hardening | Validates a V83 session, asks the existing action pipeline for a decision with enqueue disabled and no domain executor, and persists decision evidence; SQLite also defaults to :memory: | Reuse as decision-evidence/policy integration only where its caller and durable store are proven. It is not an authorization-token issuer, workload-scope registry, Action Kernel executor or replacement for canonical API authentication |
| Engineering Agent Registry | Static specialist role catalogue, capability-to-role selection and host-installed adapter registry; it reports unavailable roles and explicitly sets execution authorization false | Preserve as the AI Software Engineering role/adapter catalogue. Link role IDs to principal/tool bindings only through an explicit mapping. Never collapse specialist role metadata into workload identity or remove the engineering vertical |

#### Source-derived findings and limitations

1. **V71 is the strongest existing workforce foundation.** V71.1 and V71.2 intentionally separate identity from capability permission and both explicitly disclaim business authorization/execution authority. V71.13 composes these registries per workspace; V71.18 rehydrates them from existing Supabase tables (agent_identities, agent_capability_permissions, and related workforce tables) and verifies fingerprints before restart-safe recovery.
2. **The V71 registry classes are in-memory objects at runtime, with existing durable table hydration around them.** The inspected V71.13/V71.18 path is the composition and restore boundary. Do not infer from hydration alone that every mutation has atomic write-through, concurrency control, complete audit history or platform-wide uniqueness; verify the write adapters, schema migrations, indexes, RLS and transaction behavior in the full Gate 0 inventory.
3. **V71 identity is workspace-scoped, not a platform-wide workload principal directory.** The inspected identity record has agent ID, workspace ID, name, purpose, class, version, status, declared capabilities and metadata. It does not define an issuer subject, accountable human owner, service manifest/runtime attestation, allowed environment set, token key binding or platform-wide principal uniqueness.
4. **Fingerprints are integrity checks, not authentication.** V71 and V83/V84 use deterministic SHA-256 fingerprints. A fingerprint does not prove possession of a private key, trusted issuer identity or authenticity against an attacker able to alter both record and digest.
5. **V71.2 legacy scope semantics require careful translation.** Its scope matcher treats an empty required scope as global within that permission, still bounded by identity and governance. This must not be translated to a wildcard or unrestricted signed token claim. Migration must classify empty-scope grants, add explicit resource/workspace/environment constraints, and fail closed on ambiguous mappings.
6. **V83/V84 are complementary, not a replacement for V71.** They provide a separate tenant/workspace principal/session assurance model and decision-evidence path. Their default SQLite stores are :memory:. Tests demonstrate file-backed SQLite persistence, but that does not establish a shared production store, deployment wiring, or integration with workload tokens.
7. **V84 is decision-only in the inspected path.** It calls the action pipeline with enqueue disabled and no domain executor, then stores a decision record. It does not issue workload credentials or execute the action.
8. **The engineering Agent Registry is not an identity registry.** It catalogs specialist roles and optional installed adapters. It is useful as role metadata and execution readiness input, but not as the authoritative identity, ownership or permission record.
9. **The Advanced Agent Control Plane is an aggregation/read model.** It deliberately returns no granted authority or execution request. It can be reused for operator visibility but must not become an authorization shortcut.
10. **The inspected V71.13 composition creates a new in-memory V71 identity/permission object per workspace runtime, then hydrates existing durable records.** Runtime convergence should preserve one controlled construction path and prevent competing instances from diverging.

#### Recommended target ownership model

- **Principal identity and lifecycle:** extend/reconcile V71.1 as the canonical workforce-agent identity domain, with a thin platform-principal binding for issuer sub, actor type, accountable owner, service manifest, runtime binding and environment. If non-workforce service/scheduler actors cannot be represented safely without contaminating V71's workspace semantics, create a narrowly scoped platform-principal adapter that references V71 identities rather than replacing them.
- **Capability eligibility:** retain V71.2 for declared workforce capabilities and legacy allow/deny decisions. Add a separately versioned translation/evaluation boundary from those capability grants to the proposed authorization scope catalogue. No automatic name-based mapping and no implicit scope expansion.
- **Workload credential issuance and verification:** new, isolated issuer/verifier adapters only after trust, key custody, proof-of-possession and Render topology decisions are approved. These adapters consume the canonical identity and policy records; they do not become another registry.
- **Human identity and session assurance:** preserve the existing canonical human authentication/session path. Reuse V83 concepts or services only after confirming production wiring, durable storage, and correct distinction between human and workload principal types. Never issue workload tokens to human super-admin identities.
- **Authorization decisions and consequential governance:** retain canonical authorization/policy pipeline, Council, DecisionGate, workforce admission and Action Kernel. V84 may provide decision evidence where integrated; it must not bypass or duplicate those boundaries.
- **Engineering roles and adapters:** keep the Engineering Agent Registry and role prompt contracts intact. Map specialist role IDs to registered workload principals and exact tools at the orchestration boundary; role selection alone never grants repository, shell, workspace or deployment access.
- **Operator visibility:** retain Advanced Agent Control Plane as a read-only projection over the existing workforce control plane, enriched with principal and token status only through safe, redacted references.

#### Convergence rules

1. One canonical identity record per workload principal, with stable issuer subject mapping and accountable owner; do not create duplicate records for each runtime replica.
2. Existing V71 agent IDs and workspace ownership remain stable during migration. Any platform-level principal binding is additive and explicit.
3. Existing V71 tables and persistence remain the first reuse candidate. No new Supabase project, paid database, or duplicate registry store is assumed.
4. Existing service manifests remain authoritative for service ownership; they are referenced by principal runtime bindings, not copied into agent records.
5. Capability permissions, signed authorization scopes, tool bindings and governance approvals remain separate typed layers. No one layer silently grants the next.
6. Registry state may narrow/revoke signed authority; only a reviewed issuer can mint a new token with broadened authority.
7. Human identities, workload principals, specialist roles, runtime services and installed adapters are distinct entity types with explicit relationships.
8. Migration is incremental: shadow-map first, compare old/new decisions, migrate one actor family and surface at a time, then remove legacy bypasses only after evidence and rollback are reviewed.
9. Preserve V71 persistence and restart-safety tests, V83/V84 tenant/session and tamper tests, Advanced Agent Control Plane read-only tests, and all AI Software Engineering vertical tests.
10. No code implementation, schema migration, token issuance, permission activation or deployment is authorized by this architecture assessment.

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
- Inventory every legacy `is_species` / `species_privileges` consumer, master/Python service-token path, and platform-scope bypass; classify runtime, test, migration, documentation, and historical matches.
- Map each runtime consumer to a named workload scope, human role, or explicit removal while preserving tenant/workspace isolation and AI Software Engineering / Code Intelligence access.
- Reconcile the source-derived actor list against actual Render services, scheduled jobs, queue workers, and environment bindings; distinguish service manifests, agent principals, and capability grants.
- Inspect every Director dispatch path and downstream handler; verify the installed trace exporter/collector and evidence retention.
- Produce a reviewed consumer-to-scope migration matrix and rollback plan before implementation.
- **Exit:** reviewed component map, runtime-confirmed actor inventory, complete consumer-to-scope matrix, trace-path map, rollback plan, and no unresolved ownership conflicts.

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

**Recommendation:** approve or amend this architecture contract first. The attached Gate 0 document is an initial, focused source inspection—not an exhaustive repository/runtime inventory. After review, complete Gate 0's full read-only inventory, then proceed through separately reviewed implementation increments.


### 3.13 KDO namespace and generated-identifier convention

**Responsibility:** Make KDO-owned identities, credentials and generated resource names recognizable, collision-resistant and consistently attributable across the platform.

**Normative design rules (proposed; not yet implemented):**

- Every credential or key **issued/generated by KDO** must use a reserved KDO prefix in its externally presented identifier. The default root is `kdo_`; typed prefixes distinguish purpose and environment, for example `kdo_live_`, `kdo_test_`, `kdo_workload_`, and purpose-specific forms such as `kdo_wskey_` or `kdo_service_`. The final catalogue must be centrally versioned; examples are illustrative, not a permission or format contract.
- Prefixes apply to the public key/token identifier or credential handle. They do not require embedding the prefix into cryptographic key bytes, private-key material, passwords, third-party-issued credentials, or opaque provider secrets. Secret values must never be exposed merely to make them visibly branded.
- Every KDO-owned workspace name/slug and generated workspace identifier must carry a reserved KDO namespace, for example `kdo-ws-<opaque-id>` for machine identifiers and `kdo-<human-readable-slug>` for display slugs. Keep immutable IDs separate from mutable display names; enforce uniqueness within the authoritative workspace store.
- Apply the same ownership convention to KDO-generated agent/principal IDs, service identities, sessions, jobs, queue names, event namespaces, webhook signing-key IDs, storage prefixes and other internally generated resource handles where the underlying provider permits it. Examples: `kdo-agent-...`, `kdo-service-...`, `kdo-session-...`, `kdo-job-...`, `kdo-event-...`.
- Use lowercase ASCII, a documented delimiter, bounded length, an opaque non-secret unique suffix, and a typed namespace. Never encode secrets, personal data, tenant names, or authorization claims in an identifier. Workspace/tenant ownership must be represented and validated as structured claims/relations, not inferred from a string prefix.
- A prefix is a naming/ownership convention only. It is not authentication, authorization, proof of issuer, proof of workspace membership, or evidence that a token is genuine. Verify credentials cryptographically and evaluate scopes, lifecycle, revocation, workspace/environment boundaries and governance independently.
- KDO must reject malformed, ambiguous, reserved-prefix-conflicting and cross-tenant identifier use at creation and validation boundaries. External IDs are mapped explicitly; do not silently rewrite a third party's identifiers or treat a familiar prefix as trusted.
- Centralize generation and validation in one versioned namespace/identifier contract and shared library. Services must not invent local prefix variants or independently mint credentials. Record identifier type, issuer/owner, environment, lifecycle and provenance in authoritative metadata without storing secret material.
- Enforce environment separation in both credential policy and identifier types. Test credentials must not be accepted in staging/production, and live credentials must not be accepted in test contexts. A prefix alone is insufficient; issuer, audience, environment and key set must also be checked.

**Compatibility and migration:**

1. Inventory KDO-issued API keys, workspace IDs/slugs, agent/service IDs, session/job/event identifiers and provider-constrained names. Classify each as KDO-owned, externally issued, or legacy/unknown.
2. Define and approve the canonical prefix catalogue and grammar before changing issuance. New KDO-generated identifiers use the canonical format once the shared generator/validator is available.
3. Preserve existing immutable identifiers and active integrations during transition. Add explicit legacy-to-canonical aliases/mappings where renaming would break foreign keys, signatures, URLs, customer integrations or audit history.
4. Rotate/reissue credentials through an authorized migration, with overlap/revocation windows and audit evidence. Never transform an existing secret by string-prefixing it; issue a new credential securely and revoke the old one only after verified cutover.
5. Migrate workspace display names separately from immutable workspace IDs; preserve ownership, membership, RLS and foreign-key semantics. No bulk rename or database migration is implied by this proposal.
6. Add contract tests for generators, parsers, uniqueness, collision handling, malformed prefixes, environment confusion, cross-workspace isolation, legacy compatibility, revocation and secret redaction.

**Boundary:** This convention is cross-platform and applies to KDO-owned generated identifiers, but it does not supersede provider constraints, cryptographic identity, canonical authorization, tenant isolation or governance. It must be adopted by the API-token/workload-identity design, Platform Agent Registry, workspace model and engineering vertical without granting additional authority.

**Decision status:** proposed platform standard for review. No existing key, workspace, secret, database record or external integration is renamed, reissued, rotated or modified by this documentation.


### 3.14 Workforce persistence and runtime-convergence checkpoint

The source review confirms that V71.18 is a **workspace-scoped hydration mechanism**, not a complete persistence/write-through layer for V71 identity and permission mutations.

**Observed composition:**
- V71.13 creates the V71 identity, permission, memory, planning, evaluation, budget, takeover, coordination and bounded-autonomy registries inside `workspace_runtime(workspace_id)`.
- It caches the composed runtime in a process-local `_workspaces` dictionary keyed by workspace ID.
- When a Supabase-like client is available, V71.18 reads each existing table with an explicit `workspace_id` filter, reconstructs records, verifies fingerprints and restores them into the already-created in-memory registries.
- V71.18 does not expose durable create/update/delete methods. Its installer records whether a table-capable client is available; workspace-specific hydration happens lazily when V71.13 composes that workspace.
- V71.10 separately persists autonomous runs and uses database RPCs for lease claim/renew/finalize when a durable client is available. That run lifecycle is not a substitute for persistence of every identity/permission mutation.

**Confirmed gaps and risks requiring resolution:**
- The V71.1 and V71.2 registry classes mutate in-memory dictionaries. The inspected V71.18 component only hydrates; no canonical atomic write-through adapter for those registry mutations was established in this focused audit.
- `services/business_onboarding.py` is an existing direct writer to `agent_identities`. It upserts an `identity_fingerprint` literal `onboarding-provisioned`, rather than the SHA-256 fingerprint expected by V71.1/V71.18. A subsequent V71.18 hydration will reject that row as tampered and mark restart safety false. The writer also provisions role-derived agent IDs and does not create matching V71.2 capability permission rows in the inspected path.
- V71.13's runtime cache is process-local and has no visible synchronization around first-time lookup/construction/store. Multiple processes necessarily have separate registries; concurrent first requests in one process may also race. The staging topology previously observed runs web and worker processes separately, so this must be treated as multiple independent runtime caches, not a single shared registry.
- The V71.18 hydration tests use a lightweight fake DB/query object and verify reconstruction/fingerprint failure. They do not prove Supabase transactionality, multi-process consistency, uniqueness under concurrency, RLS behavior, or write-through.
- Migration 088 defines V71.1 identity with composite primary key `(workspace_id, agent_id)` and a workspace/status index. Migration 089 defines V71.2 permissions with a global `permission_id` primary key plus a composite uniqueness constraint. The inspected DDL does not declare a foreign key from permission rows to the matching workspace/agent identity, nor does it define RLS policies. This is a finding about these migration files, not proof that no later external policy exists.
- Migration 100 adds workspace-oriented lookup indexes only; it does not add mutation/write adapters or transaction guarantees.
- Base workspace migration 003 explicitly says RLS policies were omitted for the internal API stage. It defines workspace slug as non-null but not unique in that DDL. The workspace API performs an application-level preflight uniqueness query, but its exception is swallowed; this is not a concurrency-safe uniqueness guarantee.
- Workspace creation currently generates `ws-<random>` IDs, accepts a caller-supplied ID, and derives/sanitizes slugs without enforcing the proposed KDO namespace. Existing default workspace compatibility uses `default`.
- API-token generation currently emits raw `kny_<random>` credentials and `tok-<random>` record IDs. The table stores only the token hash and scopes it to a workspace by relation; the token ID and token secret have distinct formats and must be migrated separately.
- Other inspected generators use distinct legacy prefixes, including `onboard_` brand IDs, `plan_` plan IDs, `src_` event-source IDs, and `pub_` publishing job IDs. A separate event-source secret already uses `kdo_wh_`. This confirms that a central catalogue must account for existing typed namespaces, not flatten everything to one untyped prefix.

**Recommended convergence sequence:**
1. Inventory all writers and readers for V71 identity, permissions and every dependent workforce table, including onboarding and background processes. Classify each mutation and identify the authoritative persistence owner.
2. Repair or replace the onboarding identity writer so it constructs canonical V71 objects and computes the exact canonical fingerprint; reject placeholder fingerprints. Ensure each declared capability has a separately governed permission record where policy requires it.
3. Define a single persistence adapter contract with workspace-scoped reads and writes, deterministic serialization, fingerprint recomputation, unique constraints, optimistic concurrency/version checks, idempotency and auditable failure handling. Preserve deny precedence and quarantine ambiguous empty-scope grants.
4. Review and, if approved separately, add appropriate foreign keys, uniqueness constraints, indexes and RLS policies based on actual service-role/tenant access patterns. Do not infer authorization from workspace IDs or prefixes.
5. Define process-local cache invalidation/refresh, startup hydration readiness, cross-process consistency and a concurrency-safe runtime factory. Prove behavior across web/worker processes and restart cycles before declaring one canonical runtime.
6. Keep V71.10 run leases/RPC lifecycle separate but consistent with the same workspace and principal model. Verify RPC transaction/locking semantics and failure handling independently.
7. Only after persistence and identity convergence are proven should workload principal bindings, KDO namespace enforcement and token issuance consume the canonical registry.

**Gate 0 status:** persistence/runtime convergence remains incomplete. The above are source-level findings from selected files and migrations; no live database schema/RLS inspection, authenticated route probe, write, migration, test run or deployment was performed. No new database or paid infrastructure is proposed.
