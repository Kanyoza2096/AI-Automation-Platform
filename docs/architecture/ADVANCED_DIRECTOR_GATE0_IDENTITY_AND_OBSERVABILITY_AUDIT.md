# Advanced Director Gate 0 — Identity, Agent Registry, and Tool Observability Audit

**Audit type:** Read-only source inspection  
**Source branch:** `staging` at the time of inspection  
**Related architecture proposal:** PR #148  
**Runtime changes:** None  
**Database changes:** None  
**Deployment:** None  
**Production access or changes:** None

## Executive finding

KDO already contains useful identity, agent-permission, session-integrity, and trace-correlation components. However, the inspected code does not yet demonstrate one platform-wide autonomous-actor authority system or end-to-end Director tool-call tracing.

The four proposed improvements are therefore directly relevant. The correct path is to reconcile and extend existing components, not create parallel systems.

## 1. Workload identity and `is_species`

### Observed source behavior

- `core/species_auth.py` defines `AuthIdentity.is_species`.
- The master API token and Python service token authentication branches set `is_species=True`.
- `AuthIdentity.has_permission()` and `assert_workspace_access()` use this boolean as a broad privilege bypass.
- `services/director/api.py` has a fallback path that directly compares the supplied bearer token with `MASTER_API_TOKEN` and `PYTHON_SERVICE_TOKEN`.
- Director workspace filtering and platform mutation checks consult `request_context.species_privileges`.
- Director's authentication helper checks validity of the canonical identity when a workspace context exists, but its no-workspace fallback accepts the configured shared master/service credentials without a Director-specific scope claim.
- `core/api_tokens.py` issues opaque random `kny_` tokens and stores token hashes plus permission metadata. This is useful for workspace credentials, but it is not a signed workload token whose scopes are cryptographically bound to the token/session.
- The inspected Director and species-auth paths do not consume `director:read` or `director:dispatch` signed claims.

### Finding

**High priority:** broad species/master identity is still represented as a boolean and shared secret credential path. The Director-specific route does not yet demonstrate the proposed narrow workload principal.

### Existing components to reconcile

- `core/species_auth.py`
- `core/api_tokens.py`
- `services/v83_identity_access_hardening.py`
- `services/v71_1_agent_identity.py`
- `services/v71_2_agent_capability_permissions.py`
- `services/director/api.py`

The V8.3 identity/session component validates identity/session records and scope bindings, but its current implementation uses SHA-256 record fingerprints and a SQLite store defaulting to `:memory:`. A record fingerprint is not a cryptographic token signature, and this source inspection did not establish that the component is wired into the canonical Director authentication path.

### Required direction

1. Create a dedicated Director workload principal, separate from human accounts and Gateway identity.
2. Issue short-lived signed tokens with explicit subject, actor type, audience, issuer, scopes, workspace/platform scope, expiry, unique token ID, and key ID.
3. Verify signatures and claims using a trusted issuer/key-rotation contract.
4. Calculate effective authority as the intersection of signed scopes, active Agent Registry grants, tool/capability policy, scope boundaries, and governance.
5. Migrate all `is_species` and `species_privileges` consumers before removing compatibility fields. Do not perform a blind boolean replacement or risk a platform-wide lockout.
6. Retain workspace-scoped `kny_` credentials for their existing purpose during a controlled migration; do not silently reinterpret them as workload identities.

Initial Director scopes:
- `director:read`
- `director:dispatch`

`director:dispatch` means submit a request to the governed dispatch/admission path. It does not authorize direct handler execution, Council approval, publishing, remediation, or deployment.

## 2. Agent Registry coverage

### Observed source behavior

- `services/v71_1_agent_identity.py` provides an `AgentIdentityRegistry` with tenant/workspace-scoped registration, status transitions, and identity fingerprints.
- That registry is an in-process dictionary in the inspected implementation; its fingerprint is a deterministic integrity check, not a signed credential.
- `services/v71_2_agent_capability_permissions.py` provides a separate capability permission registry with allow/deny scope matching. It also maintains its records in process memory in the inspected implementation.
- `services/advanced_agent_control_plane.py` composes identity, permissions, planning, evaluation, budgets, takeover, and coordination components.
- `services/kdo_engineering_agent_registry.py` is a specialist-role/team selection registry for the AI Software Engineering Vertical. It is not a platform-wide inventory of every autonomous actor.
- The Director service manifest/topology contracts describe software service ownership; they do not by themselves enumerate each autonomous principal and its individual tool grants.

### Finding

**High priority:** KDO has agent identity and capability permission building blocks, but no inspected single authoritative platform-wide registry proving that every autonomous actor is known, owned, active, and restricted to an explicit tool/scope allowlist.

### Required direction

Extend or compose existing identity/control-plane components into one platform Agent Registry. Do not replace the engineering role registry; integrate it as a domain-specific inventory/projection.

Minimum discovery inventory:
- Director;
- Council actors and deliberation/decision agents;
- Content Engine and each brand scheduler actor;
- Guardian;
- AI Software Engineering planner, orchestrator, specialist agents, and worker identities;
- other scheduled, event-driven, or background autonomous actors discovered in the repository and runtime.

Each entry must identify a stable principal, owner team, lifecycle state, runtime association, environment, explicit tools, scopes, data/workspace boundary, autonomy/risk class, delegation limits, and required governance. Unknown or suspended actors must fail closed.

## 3. Director tool-call observability

### Observed source behavior

- `services/director/core.py` and `services/director/api.py` import an OpenTelemetry tracer.
- The inspected Director core/API files contain tracer initialization, but no `start_as_current_span` or `start_span` calls around Director capability/tool invocations were found.
- `docs/PHASE9_02_CORRELATED_METRICS_TRACE_CONTEXT.md` already defines `correlation_id`, `trace_id`, `span_id`, `causation_id`, and execution/evidence references.
- That Phase 9.2 document explicitly limits its status to contract/integrity scope; it does not claim deployed collectors, distributed storage, or backend-wide propagation.
- `services/director_bindings.py` defines `run_guardian_scan(params)`, calls `GuardianService.scan_all()`, and on exception returns a soft failure with a truncated message.
- The Guardian API's background scan path returns a started response; it does not itself provide Director invocation-level trace evidence.

### Finding

**High priority:** the correlation contract exists, but source inspection does not prove individual Director tool calls are instrumented end to end. The current Guardian handler can reduce a detailed exception to a short soft-failure string, making root-cause diagnosis difficult.

### Required direction

Instrument each tool invocation with a child span and structured record linked to run, agent, tool, parent span, correlation ID, authorization result, validated/redacted arguments, result summary, duration, retry count, and sanitized error classification.

Argument observability must be useful but safe:
- Preserve validated, redacted semantic arguments and a canonical argument digest.
- Never capture bearer tokens, API keys, passwords, private keys, authorization headers, or unrestricted sensitive payloads.
- Restrict stack traces and sensitive diagnostic details to authorized operators.
- Tool tracing must not grant authority or bypass policy; for consequential tools, inability to establish required audit evidence should prevent execution.

## 4. Guardian capability authority mismatch

### Observed source behavior

- `services/director/core.py` registers `run_guardian_scan` as `Authority.AUTO` and `Risk.LOW`.
- `services/director_bindings.py` binds that capability to `GuardianService.scan_all()`.
- `services/guardian_service.py` includes optional auto-fix behavior gated by `GUARDIAN_AUTO_FIX` and configuration. The flow can invoke the auto-fix engine, create GitHub PRs, notify the owner, publish an event, and record analytics.
- The Director capability's name and low-risk classification therefore do not fully describe all possible side effects of the bound service operation.

### Finding

**Critical design mismatch to resolve before broadening Director dispatch authority:** a capability presented as a scan may reach a workflow that can perform remediation side effects when configuration enables auto-fix.

### Required direction

Split the operation into separately registered and governed tools:
- `guardian:scan` — read-only discovery/reporting, no code changes or PR creation.
- `guardian:remediate` (or a more specific remediation tool) — separate capability, explicit higher risk, separate scope, Council/DecisionGate and human approval as policy requires.

Ensure scan mode cannot invoke auto-fix even when the global Guardian auto-fix setting is enabled. The Director must not inherit Guardian's broad service credentials.

## 5. Recommended implementation order

1. **Gate 0 completion:** exhaustive read-only inventory of identity/privilege consumers, service credentials, autonomous actors, tool bindings, and trace propagation.
2. **Workload identity contract:** issuer, signed claims, verification, key rotation, expiry, revocation, replay controls, and Director scopes.
3. **Agent Registry convergence:** reconcile V7.1 identity/permissions, advanced control plane, engineering roles, service manifests, and runtime actors.
4. **Tool invocation tracing:** integrate Phase 9.2 correlation context with Director capability dispatch and Guardian execution.
5. **Guardian side-effect split:** make scan strictly read-only and remediation separately governed.
6. **Compatibility retirement:** remove `is_species` authority only after call-site migration and security regression tests.
7. **End-to-end staging proof:** forged/expired/revoked token rejection, unknown actor denial, scope escalation denial, cross-workspace denial, delegation non-escalation, secret redaction, tool failure trace completeness, and no scan-to-remediation side effect.

## 6. Constraints and non-claims

- This is a source-level audit of the inspected staging branch, not a live penetration test or proof of production exploitability.
- No runtime code, credential, database, migration, deployment, or production state was changed.
- Existing identity, session, permission, correlation, and engineering-agent components must be reused where they satisfy the contract.
- No additional Supabase cost, paid infrastructure, or new database migration is approved by this audit.
- Production remains protected. Staging runtime verification is a separate later gate.
- The AI Software Engineering Vertical and its planner, orchestrator, worker runtime, Code Intelligence, and governed execution remain protected.
- Brand schedulers remain the sole owners of content generation and publishing.


## 7. Expanded authority-consumer inventory (staging source inspection)

The following is a **confirmed, focused consumer map** from direct reads of the staging branch. It is not a claim that every dynamic, indirect, generated, or plugin-local consumer has been found. The names below are grouped by the authority contract they appear to use.

### 7.1 Authentication and request-context propagation

| Source | Observed responsibility / legacy coupling |
|---|---|
| `core/species_auth.py` | Master and Python service tokens become `AuthIdentity(is_species=True)`; broad permission/workspace bypass |
| `core/api_auth.py` | Serializes `is_species` into compatibility identity data |
| `core/security/deps.py` | Converts species identity into `RequestContext.species_privileges`; exposes `require_species_privileges` |
| `core/request_context.py` | Carries `species_privileges`; broad permission, platform-grant, principal and workspace-hint behavior |
| `middleware/request_context_middleware.py` | Platform workspace binding and X-Workspace-ID behavior branch on `identity.is_species` |
| `core/workspace_context.py` | Trusted workspace context and platform workspace selection branch on `is_species` |
| `core/tenant_guard.py` | Optional cross-workspace allowance when `allow_species` is true |
| `core/auth.py` | Propagates request-context privilege into workspace context |
| `core/pipeline.py` | Platform pipeline permission and action behavior branch on `species_privileges` |

### 7.2 Platform administration, observability, and data surfaces

| Source | Observed responsibility / legacy coupling |
|---|---|
| `services/director/api.py` | Shared master/service bearer fallback; Director platform mutation/workspace checks use species privilege |
| `api/routers/users.py` | Owner-role assignment/update permits platform species privilege or workspace owner |
| `api/routers/workspaces.py` | Workspace administration/listing branches on platform privilege |
| `api/routers/posts.py` | Platform-wide post query path is gated by `species_privileges` |
| `api/routers/monitoring.py` | Exposes species privilege in platform monitoring context and response data |
| `api/slo_routes.py` | Platform SLO view requires species operator |
| `api/report_routes.py` | Platform report aggregation requires species operator |
| `api/outbox_routes.py` | Platform outbox inspection/operations require species operator |
| `api/e2e_routes.py` | Diagnostic route requires species operator and is disabled in production |
| `api/timeline_routes.py` | Workspace mismatch checks allow platform species identity |
| `api/metabolism_routes.py` | Platform plan override requires species operator |
| `api/organism_body_routes.py` | Platform organism inspection/mutation paths branch on species identity |
| `api/observability_routes.py` | Allows platform-level metrics without workspace only for species identity |
| `api/content_intelligence_automation.py` | Reads request-context platform privilege |
| `routes/autonomy_routes.py` | Elevated control-plane path treats species identity as elevated |
| `routes/backend_contract_routes.py` | Cross-workspace contract lookup bypasses mismatch for species identity |
| `routes/species_routes.py` | Platform workspace listing depends on `require_species_privileges` |
| `routes/code_intel_routes.py` | Code Intelligence API is mounted behind `require_species_privileges` |
| `routes/circulation_routes.py` | Circulation control plane uses `require_species_privileges` |
| `routes/event_api.py` | Event control plane uses `require_species_privileges` |
| `routes/cp_gap_routes.py` | Control-plane gap routes use `require_species_privileges` |
| `routes/rule_routes.py` | Rule control plane uses `require_species_privileges` |
| `routes/meta_routes.py` | Meta control plane uses `require_species_privileges` |
| `services/command_executor.py` | Command execution checks platform privilege when no workspace is bound |
| `services/intelligence_authorization_replay.py` | Records `species_privileges` as part of authorization replay context |

**Important migration implication:** not all these routes should receive Director scopes. The migration must define purpose-specific permissions for platform administration, observability, event operations, Code Intelligence, and workspace management. A Director workload credential must not inherit these unrelated platform-operator powers.

### 7.3 Other shared-secret service surfaces to reconcile

Direct source inspection also found service/master-token checks outside the central SpeciesAuth path:

- `council/routes.py` compares the master token directly for Council operations.
- `routes/council_routes.py` accepts configured master and Python service tokens in its local authentication helper.
- `core/event_api.py` has a direct master-token authentication path.
- `core/socket_server.py` maps configured master/Python service credentials to platform-level socket identity.
- `products/shared/auth.py` is a shared master-token decorator.
- `services/director/api.py` independently compares master and Python service tokens.
- `core/species_auth.py` remains the canonical validator for these shared platform credentials.

These paths need a credential-consumer inventory and explicit service-principal scopes before any shared secret is rotated or retired. This focused list is not yet exhaustive.

### 7.4 Tests and security contracts that must migrate together

The source tree includes existing regression suites for:
- `tests/test_director_auth_boundary.py`
- `tests/test_authenticated_actor_context.py`
- `tests/test_service_auth_only_workspace_bypass.py`
- `tests/test_species_scale_workspace_hydration.py`
- `tests/test_v83_identity_access_hardening.py`
- `tests/test_v84_authorization_hardening.py`
- `tests/test_v71_1_agent_identity.py`
- `tests/test_v71_2_agent_capability_permissions.py`
- `tests/test_advanced_agent_control_plane.py`
- `tests/test_c11_5_runtime_authority_convergence.py`
- `tests/security/test_control_plane_authorization.py`
- `tests/security/test_global_surface_authorization.py`
- `tests/security/test_platform_authority.py`
- `tests/security/test_plugin_isolation.py`
- `tests/tenancy/test_request_context.py`
- `tests/tenancy/test_pipeline.py`

These tests must be reviewed for assumptions about platform credentials, tenant isolation, owner roles, and engineering service access. Existing tests are not proof that the new signed-token contract is implemented.

## 8. Autonomous actor and service inventory — initial discovery

The platform service topology and runtime registries provide service-level ownership and health discovery, but they are not by themselves an actor-level security registry. The following is the initial actor map to validate with runtime owners:

| Actor / actor family | Source evidence | Proposed owner boundary | Initial scope direction |
|---|---|---|---|
| Director | `services/director/core.py`, `services/director/api.py`, `services/director_bindings.py` | Platform Intelligence / Director | `director:read`, `director:dispatch` only; dispatch to governed admission |
| Council orchestrator and decision actors | `council/orchestration/council_orchestrator.py`, `council/engine.py`, `council/routes.py` | Governance / Council | Deliberation and decision-evidence scopes; no inherited Director dispatch or publishing |
| Content Engine | `services/content_engine.py` | Content Platform | Content generation only through brand-scoped workflows |
| Brand scheduler actors | `scheduler/brand_scheduler.py`, `scheduler/post_scheduler.py` | Brand Operations | Brand/workspace-scoped schedule and governed publishing capabilities |
| Guardian scanner / remediation | `services/guardian_service.py`, `guardian/engine.py`, `guardian/auto_fix.py` | Platform Security | Separate read-only scan and separately governed remediation identities |
| Engineering planner / orchestrator | `services/kdo_engineering_agent_team.py`, `services/kdo_engineering_gateway_workflow.py`, `services/kdo_engineering_scheduler.py` | AI Software Engineering | Project/workspace/task-scoped planning and assignment; no implicit release authority |
| Engineering specialist agents | `services/kdo_engineering_agent_registry.py`, `services/kdo_engineering_agent_team.py` | AI Software Engineering | Role-specific tools and task-scoped grants |
| Engineering worker/runtime identities | `services/kdo_engineering_worker_authorization.py`, engineering worker runtime and task execution services | AI Software Engineering / Runtime Operations | Disposable workspace, task, lease and environment scoped; no host or production credentials |
| Meta Engine / autonomous loop | `services/meta_engine/core.py`, `core/autonomous_loop.py` | Platform Operations | Health/observation and governed signal publication; no content publishing authority |
| Event bus / event publishers | `core/event_bus.py`, `core/nerve.py` | Platform Infrastructure plus publisher owner | Event-type and source-scoped publish/subscribe permissions |

**Caveat:** this is a source-derived candidate inventory, not proof that every listed component currently runs as a separate autonomous principal. Some are services or orchestration components rather than independently credentialed agents. The final registry must distinguish `service`, `agent`, `scheduler`, `worker`, and `human` principals and must be confirmed against runtime deployment configuration.

### Registry model recommendation

Keep three related but distinct records:
1. **Service manifest:** software component, deployment/runtime, team owner, interfaces and health contract.
2. **Agent principal:** autonomous actor identity, owner, lifecycle, allowed environments and identity issuer binding.
3. **Capability grant:** exact tool/action, scope, risk class, expiry/version, approval policy and revocation state.

Do not infer an agent's authority from the service that hosts it. Do not register every library class as an autonomous actor. Only principals that can initiate a decision, call a tool, dispatch work, or execute an autonomous workflow need an agent-principal entry.

## 9. Observability and tracing inventory

### Confirmed existing infrastructure

- `core/event_bus.py` uses an OpenTelemetry span around `eventbus.dispatch` when tracing is installed.
- The same event bus carries correlation IDs and trace IDs in event metadata and tracks in-process correlation chains.
- `core/nerve.py` propagates correlation IDs and enforces event/workspace scope conventions.
- Director model, API, core, and utility modules initialize OpenTelemetry tracers.

### Remaining evidence gap

A focused source search found no `start_as_current_span` call in the Director tool/capability invocation path. The event bus dispatch span does not automatically capture:
- which Director capability/tool was invoked;
- the validated and redacted arguments;
- the actor and effective scopes;
- the registry and governance authorization result;
- the actual handler result or exception;
- retry and timeout details.

The absence of this call pattern in the inspected files is not proof that no lower-level instrumentation exists elsewhere. Before implementation, inspect all capability dispatch entry points and the installed exporter/collector configuration. Do not claim traces are end-to-end until a real staging run can be followed across Director → tool handler → downstream service.

## 10. Gate 0 completion status and next evidence required

**Status: PARTIALLY COMPLETE — source-level discovery only.**

Completed in this pass:
- Directly inspected canonical authentication and request-context modules.
- Confirmed multiple platform route families depend on the legacy platform privilege boolean/dependency.
- Identified several direct shared master/service token consumers outside canonical authentication.
- Mapped an initial set of autonomous actor/service families.
- Confirmed event-bus tracing and correlation infrastructure exists, while Director tool invocation tracing remains unproven.
- Identified regression suites that must be retained and extended.

Still required before Gate 0 can be marked complete:
1. Search all tracked source and configuration on the exact staging commit for `is_species`, `species_privileges`, master/Python service token comparisons, and platform-scope bypasses; classify every match as runtime, test, migration, documentation, or historical artifact.
2. Inspect all route dependencies and nested helper call paths for indirect authorization bypasses.
3. Enumerate every autonomous runtime principal from actual Render service configuration, scheduled jobs, queue workers, and environment bindings; reconcile against the code-derived candidate list.
4. Inspect all Director capability dispatch paths, handler wrappers, and downstream calls; prove where OpenTelemetry spans are created, exported, and retained.
5. Validate Guardian scan/remediation side effects with configuration variants, without running remediation against real repositories.
6. Produce a signed-off migration matrix mapping each legacy platform privilege use to a named new scope or human role, including workspace isolation behavior.
7. Define test cases and rollback sequencing before any credential cutover.

**No runtime change, migration, secret rotation, database change, staging deployment, or production operation is authorized by this audit.**


## 11. Render deployment inventory snapshot (read-only)

**Source:** connected Render workspace `My Workspace`, queried on 2026-11-11. This is a point-in-time infrastructure inventory, not a guarantee that no other external provider or manually operated worker exists.

| Render service | Type | Branch | State | Auto-deploy | Relevance |
|---|---|---|---|---|---|
| `kdo-engineering-staging` | Web service / Docker | `staging` | Not suspended | Off | Current staging runtime; one configured instance |
| `Kanyoza-Digital-organism` | Web service / Docker | `main` | Suspended by user | On (configured) | Production service; left untouched |

The connected Render workspace returned **two web services and no separately listed Render cron-job or worker services** in this inventory call. The staging service is a single web-service instance; its service metadata exposes no separate per-agent runtime identity or actor inventory.

### Implications

- The source contains internal schedulers, background loops, and agent orchestration components, but this Render snapshot does not show them as independently deployed Render services. They may execute inside the web-service process or through external providers not represented in this workspace listing.
- The Agent Registry therefore needs to register logical workload principals and runtime bindings, even when several actors share one host process. A process or container identity is not sufficient to distinguish Director, Council, Guardian, Content Engine, or individual engineering workers.
- Before token cutover, the staging runtime must identify each in-process actor at the call boundary and enforce actor-specific scopes; do not assume container-level isolation already exists.
- Production remains suspended. Its auto-deploy setting is configured as enabled, but no action was taken on it. The staging service remains on `staging` with auto-deploy disabled. No deploy was triggered.

### Runtime discovery still outstanding

- Confirm whether any queues, scheduled tasks, or external workers run outside the connected Render workspace.
- Inspect staging process startup and lifecycle registration to identify which schedulers/background loops actually start.
- Reconcile service runtime environment-variable **names and bindings** with actor identities without exporting secret values into audit records.
- Verify whether the single staging instance starts more than one scheduler/loop replica and how leases/idempotency prevent duplicate actor execution.
- Capture actor identity, runtime instance, deployment commit, and owner team in the eventual registry binding.


## 12. Follow-up authentication and runtime execution findings

This section records additional direct reads of staging source. These are code-path findings; whether each route is externally mounted/reachable and which settings are present in the live environment must still be confirmed before describing an exploitable production vulnerability.

### 12.1 Direct shared-secret authentication remains outside canonical identity

| Source | Observed authentication behavior | Migration concern |
|---|---|---|
| `services/director/api.py` | If canonical request context is absent, compares request token directly against `MASTER_API_TOKEN` or `PYTHON_SERVICE_TOKEN` | Can bypass principal claims, registry binding, and scoped authorization; should not be an alternate Director auth path |
| `core/event_api.py` | Accepts only the master token in its local `_require_auth`; has a `disable_event_api_auth` early allow branch | Must become a dedicated event API principal/scope contract; inspect whether the disable flag is configured or reachable |
| `council/routes.py` | Accepts only the master token in its local `_require_auth`; has a `disable_council_auth` early allow branch | Council needs its own principal and deliberation/decision scopes, not borrowed platform-master authority |
| `routes/council_routes.py` | Accepts either master or Python service token directly | Separate Council identity and route-specific permissions; no blanket service token |
| `core/socket_server.py` | Maps master/Python service secrets to a socket auth method string | Socket handshake should validate a canonical principal and authorize each subscribed channel/event |
| `products/shared/auth.py` | Decorator requires a non-empty token, but only rejects it when a master token is configured and does not match; when master configuration is empty, any non-empty token passes this decorator | **Fail-open conditional on missing configuration.** Confirm whether the decorated product route is mounted in the staging application; fix with fail-closed behavior before enabling that surface |
| `api/routers/ai_chat.py` | Performs local token extraction and delegates to its own authentication service | Inspect the delegated service contract and ensure the resulting principal/scopes are canonical |
| `services/kcpg_event_bridge.py` | Uses the Python service token for backend event bridge requests and also requires an event-signing secret | Preserve a narrow event-publish principal; do not retain species-wide privileges |

The canonical SpeciesAuth currently turns the master and Python service secrets into `is_species=True` with `MASTER_PERMISSIONS = [read, write, admin, owner, species]`. That is the main broad-authority conversion point. The separate direct-secret paths above mean replacing only SpeciesAuth would not complete the migration.

### 12.2 Director authentication is an independent legacy path

The Director API:
- first trusts a canonical request context only when an authenticated identity is also present;
- otherwise directly compares the bearer/API-key value against master and Python service secrets;
- uses `species_privileges` to allow platform-wide proposal/task access and platform mutations;
- permits workspace operator/admin/owner roles for some writes.

The target must be one authentication path that resolves a workload/human principal, checks the Agent Registry and capability grant, and then evaluates the exact Director action. No local master-token fallback should remain. The initial Director scopes do not authorize approval or execution of dispatched work.

### 12.3 Render process topology correction

The Render workspace has one staging web-service resource, but the Docker image starts **two Honcho Procfile processes**:

- `web`: Gunicorn with one Uvicorn worker running `main:app`.
- `worker`: `python -m services.kdo_engineering_worker_process`.

The engineering worker is explicitly inert by default. It requires `KDO_ENGINEERING_WORKER_ENABLED`, a trusted host factory, a reachable Docker daemon, and the worker database configuration before it can poll/execute. Therefore it is a separate process slot inside the same Render service/container, not a separate Render worker service. The earlier Render inventory's statement that no separate Render worker service exists remains true, but process-level worker discovery is now partially confirmed.

The main web process also composes V71 runtime convergence, runs the phased lifecycle boot, starts Meta Engine supervision, and conditionally boots schedulers according to `SERVICE_MODE`. In `full` mode, the lifecycle includes Content Engine/Autonomous Content and the autonomous loop; in `full` or `workers` mode, it includes sector reconciliation schedulers and BrandSchedulers. The Render process topology therefore does not isolate all autonomous actors into distinct OS processes.

### 12.4 Worker and scheduler identity boundaries

- The Engineering Worker process must receive a dedicated workload principal scoped to engineering task leases, approved workspace execution, and its trusted worker host. It must not inherit Director or general platform master authority.
- Content Engine and BrandSchedulers must be distinct principals. Brand scheduling/publishing remains in the brand-owned lifecycle path; Director's historical publish capability remains intentionally unbound.
- Meta Engine's service supervision identity must be observation/coordination scoped; it must not inherit content generation, remediation, or deployment authority.
- Council API authentication is currently a shared-secret boundary rather than a distinct Council principal.
- Event API publishers and socket subscribers require event-type/channel scopes, not a platform-wide operator flag.

### 12.5 Guardian scan is not side-effect-free

A direct read of `services/guardian_service.py` confirms `scan_all()` invokes a workflow that:
1. validates GitHub access and discovers repositories;
2. clones/pulls repositories and scans files;
3. may invoke the auto-fix engine and create GitHub pull requests when both auto-fix configuration and `GUARDIAN_AUTO_FIX` are enabled;
4. sends an owner notification;
5. publishes a domain event and records analytics.

The staging deployment configuration declares `GUARDIAN_ENABLED=false`, and the settings model defaults both Guardian enabled and auto-fix to false. This reduces current configured exposure but does not correct the capability contract: `run_guardian_scan` is still catalogued as `Authority.AUTO` / `Risk.LOW`. A read-only scan capability must not call the current all-in-one side-effecting workflow. Remediation must be a separate capability, principal scope, approval path, and audit record. Notifications/events/analytics should also be explicitly classified as allowed scan outputs rather than assumed to be pure observation.

### 12.6 Follow-up tests and release gates

Add regression coverage for:
- all missing-secret configurations failing closed (including the shared product auth decorator);
- disabling legacy local-auth bypasses and rejecting master/Python secrets at Director, Council, Event API, and socket entry points once migrated;
- channel/event authorization after socket authentication;
- workload identity and scope checks for worker, Council, event bridge, Meta Engine, and brand scheduler principals;
- Director's `director:dispatch` never approving, directly executing, publishing, or remediating;
- Guardian scan not invoking AutoFixEngine or creating PRs under any configuration; separately test approved remediation;
- exactly one intended scheduler/worker leader per lease/brand/task and safe duplicate startup behavior;
- trace correlation from Director capability invocation through handler and downstream service.

**Status remains PARTIALLY COMPLETE.** The Render Procfile and lifecycle give a stronger source-derived process map, but the live environment's `SERVICE_MODE`, worker enablement, external queue/scheduler providers, and route mount/reachability still need confirmation. Do not infer those values from repository defaults.
