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


## 13. Live staging route and runtime verification — 2026-11-11

This section adds read-only Render runtime evidence to the source inventory. It distinguishes the currently deployed commit from the current `staging` branch head.

### 13.1 Deployment and source drift

- Render reports staging service `kdo-engineering-staging` as not suspended, one configured instance, free plan, branch `staging`, and auto-deploy disabled.
- The latest observed live deployment was `dep-dbq140uk1f9s73da1hc0`, status `live`, started at 2026-11-11 06:58:11 UTC and completed at 06:58:50 UTC.
- That deployment runs commit `70ce311da34aa9fd1d74b2923b7d8913394b5377` (`Capture CI diagnostics and publish failure reports`). Render recorded the trigger as `service_updated` with `envUpdated=true`; this audit did not initiate that deployment.
- The current `staging` branch head at inspection is `a51d7dddc6a4ab0f229e28af8c3c17c449697e4f`, whose parent is the deployed commit. Therefore staging has advanced beyond the running deployment. Auto-deploy is off, so the current branch head is not automatically live.
- The relevant `routes/council_routes.py`, `routes/event_api.py`, and `services/director/api.py` blobs are identical between deployed commit `70ce311` and current staging head `a51d7dd`. `main.py` differs because the later staging commit includes the plugin-retirement route cleanup. This audit does not assume all application code on the branch is live.

### 13.2 Live startup and process evidence

Render application logs from the latest live instance confirm:

- Boot environment is `staging`; runtime version log reports `14.7.5`, Python `3.12.16`, and `SERVICE_MODE=full`.
- Director proxy is disabled because Director runs in-process. The logs confirm Director routes mounted directly at `/director/*`.
- The Event API logs confirm routes mounted at `/api/v1/events/*`.
- The Council control-plane router logs confirm it mounted successfully.
- The Honcho `worker.1` process logs: `Engineering worker is disabled; waiting without polling.` Thus the worker process slot exists, but it is not polling or executing engineering jobs in this runtime snapshot.
- Application startup completed at 2026-11-11 06:59:13 UTC.
- The latest sampled health-monitor cycle at 07:44:28 UTC reported 20 services total, 19 OK, 0 down, and 1 unknown (`Plugin:facebook`). This is the platform health monitor's reported snapshot, not an independent probe of every service.
- Brand scheduler logs show a brand named `KDO Semantic Evaluation Lab` being skipped because it has no AI profile. This is a configuration/eligibility observation, not evidence of a platform-wide scheduler failure.

### 13.3 Route mounting versus endpoint reachability

Confirmed by the deployed process's startup logs and source:

| Surface | Mount evidence | Authentication evidence | Gate 0 implication |
|---|---|---|---|
| Director `/director/*` | Mounted directly in the live process | Director source retains a direct master/Python service-token fallback when canonical request context is absent | Remove fallback and require Director workload identity plus narrow scopes |
| Council `/api/v1/council/*` | Council router mounted; `main.py` includes `routes.council_routes` | `routes/council_routes.py` directly accepts configured master or Python service token | Confirmed mounted shared-secret consumer; migrate to a distinct Council principal |
| Event API `/api/v1/events/*` | Live startup log confirms mount; `main.py` registers `routes.event_api` | `routes/event_api.py` validates through canonical `SpeciesAuth`; publish/clear additionally require species privilege | Migrate publisher and reader permissions to event-type/channel scopes |
| Legacy `core/event_api.py` | Not the module registered by the inspected `main.py` mount function; main registers `routes.event_api` | Contains a separate local master-token path and `disable_event_api_auth` bypass | Do not describe this duplicate helper as live-reachable through the confirmed Event API mount |
| Product shared auth helper | Its own module docstring identifies it as legacy Flask auth not used by the FastAPI main app; it is not in the inspected main route-module list | Missing master configuration permits any non-empty token through this decorator | Conditional fail-open remains a code defect; no evidence here that it is mounted in the live FastAPI app |

The Director, Council, and Event API mounts are confirmed from startup logs. **Authenticated endpoint behavior was not exercised.** The available request-log query returned no request records for the queried window, and direct public HTTP inspection was unavailable in this audit session. Do not equate route mounting with a successful authenticated request or with proof of external reachability through every proxy.

### 13.4 Runtime configuration visibility and limits

- The live startup log establishes `SERVICE_MODE=full`; it is stronger evidence than the repository default.
- The worker's explicit disabled log establishes that it did not poll in this snapshot. The raw environment-variable values were not read or copied into this audit.
- Render service metadata and logs do not establish the values of every Guardian, scheduler, external queue, or integration environment variable. No secret values were requested or exposed.
- The connected Render workspace inventory still contains one staging web-service resource, not separate Render worker/cron resources. The two Procfile processes share the service/container; multiple in-process actors and lifecycle schedulers are active in `full` mode.
- External queue providers, manually operated workers, and schedulers outside this Render workspace remain unverified.
- No authenticated route probes, writes, remediation calls, database queries, environment-variable changes, deployments, or production operations were performed by this audit.

### 13.5 Gate 0 status after runtime verification

**Status remains PARTIALLY COMPLETE.** Runtime verification now confirms the active service mode, the disabled engineering worker, and successful startup mounting of Director, Council, and Event API surfaces. It also confirms a staging deployment lag: the live service runs `70ce311`, while the branch head is `a51d7dd`.

Remaining work includes:
1. Reconcile the live deployment commit with the intended staging source before treating branch-only route changes as runtime behavior. Any deployment remains a separate explicitly authorized action.
2. Perform safe authenticated route tests in staging using dedicated test principals, once those principals and non-production credentials are approved.
3. Finish exact-commit source/config classification and the consumer-to-scope migration matrix.
4. Verify socket handshake/channel authorization, external workers/providers, trace exporter/collector behavior, and Director-to-tool downstream trace continuity.
5. Complete the Guardian scan/remediation boundary and migration rollback plan.

No runtime implementation, credential rotation, database change, deployment, or production action is authorized by this audit.


## 13. Consumer-to-scope migration matrix — proposed authorization contract

**Status:** design mapping for Gate 0 review; scope names below are proposed vocabulary, not claims that these scopes already exist in code or tokens. This matrix is intentionally deny-by-default. Each legacy consumer must be migrated and regression-tested before its compatibility bypass is removed.

### 13.1 Principal classes and authority rules

| Principal class | Intended authority source | Explicit boundary |
|---|---|---|
| Human workspace member | Supabase-authenticated subject + accepted workspace membership + workspace role | Workspace only; workspace owner/admin must not imply platform-wide authority |
| Human platform operator | Server-side UUID allowlist + named platform scopes | Only the listed platform operations; no implicit wildcard |
| Human platform super-admin | Server-side UUID allowlist + explicit break-glass platform grant | Human-only; audited, short-lived where feasible; never copied to machine identities |
| Director workload | Signed workload token + active Agent Registry grant + tool policy | Initially `director:read`, `director:dispatch`; dispatch submits to governed admission only |
| Council workload | Separate Council principal + Council policy grant | Deliberation/evidence/decision workflow only; approval remains a distinct governed action |
| Guardian scanner | Separate read-only Guardian principal | Scan/report only; no remediation, PR creation, or write authority |
| Guardian remediation | Separate higher-risk principal and capability | Explicit remediation scope, decision reference, Council/DecisionGate and human approval as policy requires |
| Content Engine / brand scheduler | Separate content or scheduler principal, bound to brand/workspace | Content generation and publishing remain owned by the Content Engine and brand schedulers, not Director |
| KCPG event bridge | Dedicated bridge principal with signed event envelope | Allowlisted event types and explicit workspace binding; no general platform operator authority |
| Engineering planner / specialist / worker | Engineering vertical identity plus role/tool grants and execution policy | Project/workspace sandbox and task-specific grants; no inherited platform admin or production deployment authority |

Effective machine authority is the intersection of verified signed claims, active Agent Registry grants, tool/capability policy, workspace/environment boundaries, and required governance. Registry state may revoke or narrow a grant; it must never broaden signed authority. Missing, unknown, expired, revoked, mismatched, or unverifiable identities fail closed.

### 13.2 Legacy consumer mapping

| Legacy consumer / surface | Current coupling observed | Proposed replacement contract | Migration owner / guard |
|---|---|---|---|
| `core/species_auth.py` master-token branch | `is_species=True`, broad master permissions and cross-workspace bypass | Human operator or dedicated machine principal validation; no shared boolean privilege | Identity platform; preserve human operator break-glass separately |
| `core/species_auth.py` Python service-token branch | Same species-wide identity as master token | Replace each integration with a named service principal and minimal scopes | Identity platform + each service owner |
| `core/api_auth.py` | Serializes `is_species` | Serialize principal type, subject, verified scopes and bounded workspace/environment claims; compatibility field read-only during migration | Auth platform |
| `core/security/deps.py` | Builds `species_privileges`; exposes `require_species_privileges` | Scope dependencies such as `require_scope("platform:...")` and explicit human-role checks | API/security platform |
| `core/request_context.py` | Boolean privilege bypass in permissions, workspace assertion and workspace hint selection | Typed principal + verified scope set + explicit workspace/environment boundary; no caller-controlled privilege hint | Request-context owners |
| `middleware/request_context_middleware.py`, `core/workspace_context.py` | Species identity can select platform workspace context | Resolve workspace from verified claim or authorized human selection; platform-wide reads require a named scope | Tenancy owners |
| `core/tenant_guard.py` | `allow_species` can bypass workspace mismatch | Replace with explicit cross-workspace scope checked against target resource and audit reason | Tenancy/security owners |
| `core/auth.py`, `core/pipeline.py` | Species privilege influences generic write authorization and decision override | Domain action permissions and explicit decision provenance; no generic machine override | Pipeline/action-kernel owners |
| `api/routers/users.py` | Platform privilege can affect user role assignment/update | `platform:users:read` / `platform:users:manage`; workspace owner limited to workspace membership administration | Identity administration |
| `api/routers/workspaces.py` | Platform-wide workspace listing/admin | `platform:workspaces:read` and `platform:workspaces:manage`; separate human/operator policy | Workspace platform |
| `api/routers/posts.py` | Platform-wide post query path | `platform:posts:read`; content mutation stays with brand-scoped workflow | Content platform |
| `api/routers/monitoring.py`, `api/observability_routes.py` | Species privilege exposes platform monitoring/metrics | `platform:monitoring:read`, `platform:observability:read`; redact tenant-sensitive fields | SRE/observability |
| `api/slo_routes.py`, `api/report_routes.py` | Platform SLO and report aggregation | `platform:slo:read`, `platform:reports:read`; export/download may require separate scope | SRE/reporting |
| `api/outbox_routes.py` | Platform outbox inspection/operations | Separate `platform:outbox:read` and `platform:outbox:operate`; operation requires idempotency and audit evidence | Messaging platform |
| `api/e2e_routes.py` | Diagnostic route uses species operator | `platform:diagnostics:run`, non-production environment guard, explicit operator authorization | QA/SRE |
| `api/timeline_routes.py`, `routes/backend_contract_routes.py` | Workspace mismatch bypass for platform principal | Explicit `platform:timeline:read` or `platform:contracts:read` plus resource-level tenancy checks | Platform API |
| `api/metabolism_routes.py` | Platform plan override | `platform:metabolism:override`, high-risk decision gate and human approval | Metabolism owner |
| `api/organism_body_routes.py` | Platform organism inspection and mutation share species check | Split `platform:organism:read` from `platform:organism:operate`; mutation governed separately | Platform operations |
| `api/content_intelligence_automation.py` | Reads request-context platform privilege | Read-only insight scopes separated from content mutation; brand/workspace-scoped actions | Content intelligence |
| `routes/autonomy_routes.py` | Elevated control-plane path treats species as elevated | Explicit control-plane read/operate scopes per operation; autonomy changes require governance | Autonomy platform |
| `routes/species_routes.py` | Platform workspace listing via `require_species_privileges` | `platform:workspaces:read`; do not grant to Director by default | Platform API |
| `routes/code_intel_routes.py` | Code Intelligence API behind species privilege | Dedicated `engineering:code_intel:read` / `engineering:code_intel:analyze`; execution remains in engineering policy | AI Software Engineering Vertical |
| `routes/circulation_routes.py` | Circulation control plane | `platform:circulation:read` / `platform:circulation:operate`, with operation-specific governance | Circulation owner |
| `routes/event_api.py` | Event control plane uses species privilege | Split `events:read`, `events:publish`, `events:admin`; event-type and workspace allowlists | Event platform |
| `routes/cp_gap_routes.py` | Control-plane gap operations | `platform:control_gaps:read` / `platform:control_gaps:manage` | Platform governance |
| `routes/rule_routes.py` | Rule control plane | `platform:rules:read` / `platform:rules:manage`; changes require versioning and review | Policy/rules owner |
| `routes/meta_routes.py` | Meta control plane | `platform:meta:read` / `platform:meta:operate`; recovery/remediation separately gated | Meta Engine owner |
| `services/command_executor.py` | Platform privilege when no workspace is bound | Named command IDs, explicit `platform:commands:execute`, allowlisted arguments, environment boundary and approval policy | Execution platform |
| `services/intelligence_authorization_replay.py` | Records boolean species privilege | Record principal subject/type, verified scope snapshot, policy version and decision reference; redact secrets | Audit/observability |
| `services/director/api.py` | Direct master/Python token fallback; species privilege controls platform task/proposal visibility and mutation | Remove alternate shared-secret path; Director read endpoints require `director:read`; dispatch endpoints require `director:dispatch`; approval/execute endpoints require separate governance and must not be granted to Director | Director + identity platform |
| `council/routes.py` | Direct master token; configurable `disable_council_auth` bypass | Dedicated Council service identity and narrow Council route scopes; configuration must never disable production auth | Council owner |
| `routes/council_routes.py` | Direct master/Python service-token acceptance | Same Council principal contract; do not treat Gateway identity as Council identity | Council owner |
| `core/event_api.py` | Direct master-token auth; configurable `disable_event_api_auth` bypass | Dedicated event publisher/consumer principals and route-specific event scopes; auth bypass prohibited in production | Event platform |
| `core/socket_server.py` | Master/Python token maps to broad socket identity | Canonical principal validation plus per-channel and per-event authorization; no broad socket identity from shared secret | Realtime platform |
| `products/shared/auth.py` | Shared master-token decorator; if master secret is unset, any non-empty token passes its conditional comparison | Replace with canonical verifier; reject if issuer/configuration unavailable; first establish whether the Flask product app is mounted/reachable | Product platform; route reachability still requires proof |
| `services/kcpg_event_bridge.py` | Python service token plus HMAC event signature | Dedicated `kcpg-event-bridge` principal with `events:publish`, workspace/event allowlists, signature verification and replay protection; retain HMAC during staged migration | KCPG bridge owner |
| `services/guardian_service.py` via Director binding | Scan handler reaches optional auto-fix, notifications, events and analytics | Strictly read-only `guardian:scan`; separate `guardian:remediate` tool and identity; classify permitted scan outputs | Guardian/security owner |

**Scope naming note:** the proposed scope strings are a design vocabulary for migration planning. Before implementation, normalize them in one versioned scope catalogue with resource/action semantics, risk class, and owning team. Avoid introducing overlapping aliases or wildcard grants.

### 13.3 Director capability boundary

| Capability class | Director may do? | Required authority |
|---|---|---|
| Read platform health, approved observations and Director state | Yes, within configured read surface | `director:read` plus resource policy |
| Submit a typed task/proposal to governed admission | Yes | `director:dispatch` plus schema validation and admission policy |
| Directly invoke arbitrary capability handlers | No | Not granted |
| Approve its own proposal or Council decision | No | Separate eligible human/Council authority |
| Publish content or post to a brand | No | Brand scheduler / Content Engine principal |
| Run Guardian remediation or create a fix PR | No | Separate remediation principal + governance |
| Change platform rules, identities, scopes or credentials | No | Human platform authority and change governance |
| Deploy to production or modify production infrastructure | No | Explicit human authorization and release controls |

### 13.4 Regression-test migration matrix

| Test family | Required assertions before retiring compatibility |
|---|---|
| `test_director_auth_boundary.py` | Director token with read cannot dispatch; dispatch cannot approve/execute; no master/Python fallback; workspace/environment mismatch denied |
| `test_authenticated_actor_context.py` | Human, service, agent, scheduler and worker principal types remain distinct; subject and scope provenance preserved |
| `test_service_auth_only_workspace_bypass.py`, `test_species_scale_workspace_hydration.py` | No implicit cross-workspace access; only explicit bounded scope can cross; client workspace header cannot broaden authority |
| `test_v83_identity_access_hardening.py`, `test_v84_authorization_hardening.py` | Expired, forged, revoked, wrong-audience, wrong-issuer, wrong-key and replayed workload tokens rejected |
| `test_v71_1_agent_identity.py`, `test_v71_2_agent_capability_permissions.py`, `test_advanced_agent_control_plane.py` | Unknown/suspended agent denied; Registry grants cannot broaden signed scopes; role delegation cannot escalate |
| `test_c11_5_runtime_authority_convergence.py` | Actor identity remains consistent across API, event, worker and scheduler execution boundaries |
| `tests/security/test_control_plane_authorization.py`, `test_global_surface_authorization.py`, `test_platform_authority.py`, `test_plugin_isolation.py` | Each mapped platform surface checks its specific scope; no machine principal inherits human super-admin |
| `tests/tenancy/test_request_context.py`, `tests/tenancy/test_pipeline.py` | Request context and pipeline use typed scopes; no species boolean bypass; generic write does not imply platform operation |
| New Council/event/socket tests | Missing credentials, disabled-auth flags, wrong principal, wrong event/channel scope and invalid signatures fail closed |
| New Guardian contract tests | Scan cannot invoke AutoFixEngine/create PRs under any config; remediation requires its own principal and approval evidence |
| New audit/redaction tests | No secrets in spans/logs; consequential execution fails closed if required authorization/audit evidence is absent |

### 13.5 Migration sequencing and rollback gates

1. Freeze the proposed scope catalogue and assign a named owner to every consumer row.
2. Add typed principal/scope fields alongside existing compatibility fields; shadow-evaluate decisions without changing grants.
3. Add per-route policy checks and regression tests. For each surface, compare old and new decisions and investigate every mismatch.
4. Migrate machine integrations individually: Director, Council, KCPG bridge, Guardian, engineering actors, schedulers and socket clients. Do not rotate shared credentials until all consumers and callers are accounted for.
5. Enable enforcement in staging first. Unknown actors, missing claims, invalid signatures, missing workspace bindings and audit failures must deny.
6. Remove each legacy bypass only after its consumer has a verified replacement, tests pass, and rollback procedure is documented.
7. Retire `is_species` and `species_privileges` only when repository-wide and runtime inventories show no remaining authorization consumers.

Rollback must restore only the last known compatible authorization policy for the affected surface, never a global species-wide bypass. Keep production untouched; no database migration or new paid infrastructure is implied by this design phase.

## 14. Authorization scope catalogue — design review checkpoint

The companion architecture proposal now defines the proposed catalogue kdo-authz-scopes/v1. This is a design vocabulary only: no scope is active, assigned, issued in a token, or enforced by this documentation change.

Review requirements before Gate 0 exit:

- Confirm the owning team for every scope and identify a named accountable human owner; unresolved ownership blocks activation.
- Reconcile each scope to exact route, capability, tool, event type/channel, resource class, workspace, environment and runtime consumer. The catalogue is not a substitute for that exhaustive mapping.
- Validate that the Director's initial authority is exactly director:read and director:dispatch, with dispatch limited to governed admission.
- Validate human-only critical authority boundaries, especially identity:registry:manage, production deployment, merge authority and credential/key operations.
- Confirm Guardian scan/remediation, Content Engine generation/Brand Scheduler publication, Council deliberation/decision, and engineering planning/write/execute/merge/deploy are separate permissions and principals.
- Confirm KCPG event publishing and socket subscriptions are constrained by event type, channel, direction, source identity and workspace.
- Confirm the risk tier is calculated from the concrete operation and target; actor labels or tool metadata cannot lower it.
- Specify issuer, audience, proof-of-possession, key rotation/revocation and catalogue-version pinning in the token implementation contract before any runtime rollout.
- Review the catalogue for over-broad resource labels, missing owner teams, duplicate semantics and scope/action mismatch; add only scopes backed by an identified consumer.

**Decision status:** pending owner/security/governance review. Gate 0 remains partially complete. This checkpoint does not authorize implementation, token issuance, permission grants, database changes, deployment, or production activity.

## 15. Workload-token and Agent Registry contract review checkpoint

The companion architecture proposal now defines the proposed KDO Workload Token Profile v1 and a logical Platform Agent Registry schema. Both remain design contracts only; neither is implemented, provisioned, migrated or enforced.

### Required protocol decisions before implementation

- Confirm the trusted workload identity issuer and its runtime attestation/authentication mechanism. Do not accept caller-supplied agent IDs, scope lists or identity headers.
- Select and validate one proof-of-possession mechanism against the actual Render/network topology: end-to-end mTLS certificate binding or a workload-held asymmetric key with a rigorously specified per-request proof. Do not assume proxy-terminated TLS provides workload identity.
- Confirm exact token type, allowed asymmetric algorithm, trusted issuer/key distribution, narrow service audiences, claim schema, scope catalogue version and authorization-policy version.
- Define clock-skew limits, token maximum lifetime, replay cache/nonce behavior, session binding, revocation freshness objective and compromised-key response.
- Define verifier behavior when the registry, issuer metadata, revocation state or audit infrastructure is unavailable; consequential actions must fail closed.
- Validate key custody and rotation without storing private keys, signing secrets or access tokens in application configuration, registry records, logs or traces.

### Required registry decisions before implementation

- Reconcile V71 identity and capability permission registries, the engineering Agent Registry and current session hardening; identify the canonical owner/storage/API and avoid a competing in-memory or database source of truth.
- Confirm which source/runtime components are independently acting principals versus passive libraries, services or orchestrators. Require a named accountable human owner for every active principal.
- Confirm principal subject uniqueness, runtime binding, lifecycle transitions, grant/tool/resource/workspace/environment relations, delegation, approval provenance, audit retention and emergency suspension semantics.
- Define a measurable revocation objective and prove it across API, socket, event, worker and scheduler boundaries.
- Confirm that registry grants and caches can narrow/revoke signed authority but can never broaden it; broadening requires reviewed grant change and new token issuance.
- Keep human identities separate from workload identities. Human super-admin, identity management, production release and critical credential operations must never be copied into workload tokens.
- Confirm that no physical database schema or migration is selected until existing registries and persistence are assessed. No additional paid infrastructure or Supabase cost is authorized.

### Acceptance evidence required

- Positive and negative claim/proof-of-possession tests, including algorithm confusion, forged signature, wrong issuer/audience, expiry, replay, key rotation/revocation, proof-key mismatch and canonical request binding.
- Agent Registry tests for uniqueness, ownership, lifecycle, missing runtime, suspended/retired actors, stale approvals, scope narrowing, delegation boundaries and immutable audit provenance.
- Cross-surface identity convergence tests for API, socket, event, worker and scheduler execution.
- Evidence that unknown actors, registry outage and revocation uncertainty cannot fall back to master/Python shared credentials or is_species bypasses.
- Evidence that Director receives only director:read and director:dispatch and that dispatch cannot directly execute, approve, publish, remediate or deploy.

**Decision status:** pending architecture, identity/security, runtime and governance review. Gate 0 remains partially complete. No token issuer, verifier, registry implementation, migration, grant, test run, deployment or production action is authorized by this checkpoint.

## 16. Existing identity infrastructure convergence checkpoint

A focused source inspection of the staging branch was completed for V71.1, V71.2, V71.13, V71.18, the Advanced Agent Control Plane, V83, V84, the engineering role registry and their directly relevant tests. This is not a repository-wide persistence, migration, RLS, route-reachability or production-wiring audit.

### Confirmed source-level findings

| Component | Evidence-based finding | Gate 0 implication |
|---|---|---|
| V71.1 | Workspace-scoped agent identity/lifecycle and declared capabilities; SHA-256 fingerprint; explicit no-authority/no-execution methods | Strong reusable identity domain model, but lacks platform subject, accountable owner, runtime attestation, environment and key binding |
| V71.2 | Workspace-scoped capability allow/deny grants; declared-capability validation; explicit deny wins; empty permission scope matches globally within that permission subject to identity/governance | Reuse as a capability-policy input, but empty scopes and legacy capability names require explicit migration classification; never translate to wildcard token scopes |
| V71.13 | Creates V71.1/V71.2 in-memory registries per workspace runtime, then composes workforce services; fail-closed on durable hydration failure | Preserve as the single runtime composition path; prevent duplicate registry instances |
| V71.18 | Hydrates identities, capability permissions and workforce records from workspace-filtered Supabase tables, verifies record fingerprints, and reports restart safety | Existing durable workforce tables are the first persistence reuse candidate; hydration alone does not prove all write, transaction, uniqueness, RLS or audit semantics |
| Advanced Agent Control Plane | Read-only, fingerprinted snapshot over existing registries; authority_granted and execution_requested remain false | Reuse as an operator/read-model projection, never as an authorization service |
| V83 | Tenant/workspace-bound identity and access sessions, expiry/state checks and integrity fingerprints; SQLite defaults to :memory: | Reuse assurance concepts/tests only after confirming production configuration and durable shared storage |
| V84 | Validates V83 sessions and asks the action pipeline for a decision with enqueue disabled and no domain executor; records decision evidence; SQLite defaults to :memory: | Decision-evidence component only; not token issuance, workload scope registry or action execution |
| Engineering Agent Registry | Static role catalogue and host-installed adapter registry; selected role readiness is distinct from execution authorization | Preserve as specialist role metadata; map roles to workload principals/tools explicitly without replacing the engineering vertical |

### Convergence decision

**Recommended:** keep V71 as the canonical workforce-agent identity/capability foundation and extend it through a narrow platform-principal binding. Retain V71.13/V71.18 as the runtime composition and durable hydration path. Reuse V83/V84 only for their distinct identity/session assurance and authorization-decision evidence responsibilities after wiring/storage verification. Retain the Engineering Agent Registry as role/adapter metadata. Add workload-token issuer/verifier adapters only after proof-of-possession and key custody decisions; do not create a parallel registry database.

### Unresolved verification items before Gate 0 exit

- Trace every V71 registry mutation to its durable write adapter and confirm transaction boundaries, optimistic/concurrent update behavior, uniqueness/indexes, foreign keys, schema migration history and Supabase RLS.
- Confirm V71.18 hydration and all write paths behave consistently across multiple web/worker processes and runtime restarts; inspect stale cache and duplicate-runtime behavior.
- Determine whether V83/V84 are wired into live API authentication or remain separately callable components; inspect production storage configuration without reading secret values.
- Reconcile V71 workforce agent IDs to actual engineering worker/planner/specialist runtime actors and determine whether they are independent principals or role metadata.
- Establish canonical mapping between human identity, workspace API-token identities, non-human service credentials and future workload principal subjects without reusing human super-admin or shared master/Python service credentials.
- Confirm exact accountable owner and runtime binding for each actor family; source labels alone are not evidence of an independently authenticated principal.
- Review all relevant table migrations and RLS policies, not just Python hydration code. No live database queries or mutations were performed in this focused source review.

### Required migration safety

- Preserve existing V71 agent IDs, workspace boundaries, capability declarations, planning, memory, budgets, takeover, coordination, durable runtime and worker governance.
- Shadow-map V71 capability grants to the new scope catalogue; explicit-deny precedence remains. Empty-scope grants are quarantined for review until resource boundaries are made explicit.
- Keep signed scopes, capability eligibility, tool bindings, registry constraints and governance approval as distinct layers.
- Never treat SHA-256 record fingerprints as signatures, issuer authentication or proof-of-possession.
- Preserve V83/V84 tenant/session and tamper tests, V71 persistence/restart tests, Advanced Agent Control Plane read-only tests and the entire AI Software Engineering / Code Intelligence suite.
- No new Supabase project, paid database, migration, token issuance, grant activation, runtime code, test run, deployment or production operation is authorized by this checkpoint.

**Decision status:** architecture recommendation documented; owner/security/runtime review pending. Gate 0 remains partially complete because persistence write paths, migrations/RLS, complete actor mappings and live wiring are not yet exhaustively verified.



## 17. KDO namespace and generated-identifier standard checkpoint

A cross-platform naming requirement has been added to the Advanced Director proposal: KDO-generated credentials and internal resource identifiers must be visibly attributable to KDO through a reserved, typed namespace. This is a design checkpoint only; no identifiers or credentials were changed.

### Proposed convention

- KDO-issued API keys and tokens use a reserved `kdo_` root with centrally defined typed variants (illustrative: `kdo_live_`, `kdo_test_`, `kdo_workload_`, `kdo_wskey_`). Credential handles/key IDs are prefixed; cryptographic key bytes and private material are not modified or exposed to satisfy naming.
- KDO-owned workspace machine IDs and slugs use reserved KDO forms (illustrative: `kdo-ws-<opaque-id>` and `kdo-<slug>`). Immutable IDs remain distinct from display names.
- KDO-generated agent, service, session, job, event, webhook-key and storage identifiers use centrally registered typed namespaces where supported.
- Identifiers use a bounded lowercase ASCII grammar, safe opaque suffixes and no embedded secrets, personal data or authorization claims. Tenant ownership is a structured relation/claim, never inferred from a prefix.
- Prefixes are not credentials and confer no trust or authority. Cryptographic verification, issuer/audience/environment checks, scopes, revocation, workspace isolation and governance remain mandatory.
- Externally issued/provider-owned identifiers are mapped, not rewritten. Provider naming constraints must be respected.

### Migration and verification gates

- Inventory all KDO-issued credentials and KDO-owned workspace/resource identifiers; classify external and legacy identifiers separately.
- Approve one central prefix catalogue, generator and validator before enforcing issuance.
- New KDO-generated identifiers adopt the standard after shared implementation is reviewed.
- Preserve existing immutable IDs and integrations using explicit aliases/mappings where needed. Credential changes require secure reissuance, verified cutover, overlap/revocation planning and audit; never prefix an existing secret in place.
- Workspace renames require separate review of foreign keys, membership, RLS and tenant isolation. No bulk rename or schema migration is authorized here.
- Add tests for type grammar, uniqueness/collisions, environment separation, malformed identifiers, cross-workspace isolation, legacy compatibility, revocation and secret redaction.

### Scope and status

This standard is intended to be cross-platform, including the API-key/workload-token contract, Platform Agent Registry, workspace model and engineering vertical. The examples are illustrative until a versioned catalogue is approved. It does not authorize implementation, key rotation, credential issuance, database changes or deployment. Gate 0 remains **PARTIALLY COMPLETE**.


## 18. V71 persistence, workspace and credential-generator convergence audit

This checkpoint records a focused read-only source inspection of V71.1/V71.2, V71.13/V71.18, V71.10, the direct onboarding writer, workspace creation/model/migration and API-token generation. It is not a complete repository-wide writer inventory or live database/RLS audit.

### Confirmed persistence/runtime behavior

- V71.13 creates workspace-scoped registries and caches each composed runtime in a process-local dictionary keyed by workspace ID. There is no visible lock around cache miss → composition → cache insertion.
- V71.18 reads the nine workforce tables with an explicit workspace filter, reconstructs the in-memory objects and validates their record fingerprints. It is hydration-only; it does not implement durable mutation methods.
- V71.18's installer marks whether a table-capable DB client is present. Actual per-workspace hydration is lazy, inside V71.13 runtime composition.
- V71.10 persists autonomous runs and uses RPCs for durable lease operations when the DB client is available. This is a separate run-lifecycle persistence path, not general V71 identity/permission write-through.
- Existing V71.18 tests use fake query/database classes. They cover empty hydration, fingerprint rejection and non-empty reconstruction, but do not prove real transaction, RLS, concurrent mutation or multi-process semantics.

### Migration and schema findings

| Source | Confirmed definition | Gap / implication |
|---|---|---|
| Migration 088 | `agent_identities`; composite primary key `(workspace_id, agent_id)`; workspace/status index | No RLS policy in this migration |
| Migration 089 | `agent_capability_permissions`; global `permission_id` primary key; unique workspace/agent/capability/permission tuple | No declared FK to identity and no RLS policy in this migration |
| Migration 100 | Adds workspace-oriented indexes for hydration/restart inspection | Does not supply mutation persistence or transaction guarantees |
| Migration 003 | Workspace ID primary key; slug non-null; workspace member FK; comment explicitly says RLS policies omitted at that stage | Slug is not unique in this DDL; application preflight is not concurrency-safe |
| Migration 006 | API token record ID primary key; workspace FK with cascade; token hash unique | Raw token is not stored; ID and credential secret have separate naming formats |

These are observations about the inspected migration files only. Later migrations or Supabase dashboard policies may add controls; those have not been exhaustively inspected or queried live.

### Direct writer and runtime risks

1. **Onboarding fingerprint incompatibility:** `services/business_onboarding.py` directly upserts `agent_identities` and writes the literal fingerprint `onboarding-provisioned`. V71.1 and V71.18 expect a canonical SHA-256 fingerprint over the identity payload. Therefore this row will fail V71.18 integrity validation on hydration unless another process rewrites it first. The inspected path does not establish such a rewrite.
2. **Permission convergence:** the onboarding path declares worker capabilities but the inspected code does not write corresponding V71.2 permission records. Declared capability is not a grant; this separation must be preserved and explicit grants must be governed.
3. **Runtime multiplicity:** V71.13 cache is process-local. Separate web and worker processes create separate runtime/registry instances and independently hydrate. First-use construction is not visibly synchronized inside a process. A distributed registry cannot be inferred from this cache.
4. **Persistence gap:** V71.1/V71.2 registries mutate in-memory dictionaries. V71.18 restores them from durable tables but does not provide a matching general write-through adapter. The canonical owner of all mutations remains unresolved.
5. **Database controls:** the inspected V71 migrations do not define RLS or an identity FK for permissions. Migration 003 explicitly records that RLS was omitted at that stage. Full later migration and live-policy verification remains outstanding.

### Existing KDO naming inventory (partial)

- Workspace API: default ID `ws-<random>`; caller-supplied IDs are accepted; generated slug is sanitized but no KDO prefix is enforced. The model retains a `default` compatibility workspace and does not require a namespace prefix.
- API credentials: `core/api_tokens.py` issues `kny_<random>` plaintext credentials, stores only SHA-256 hashes, and assigns `tok-<random>` record IDs.
- Onboarding: brand IDs use `onboard_`; plan IDs use `plan_`; workforce IDs use a role/workspace concatenation.
- Event sources: an existing KDO-owned webhook secret uses `kdo_wh_`, while its source ID uses `src_`.
- Brand scheduler: publishing job IDs use `pub_`.

This is a sample inventory from targeted searches, not an exhaustive count of all generated identifiers. It demonstrates that KDO already has a few KDO-prefixed formats alongside legacy prefixes. The namespace standard must preserve typed distinctions, identify externally/provider-issued values, and migrate credentials by secure reissuance rather than editing secrets in place.

### Required next gates

- Complete a writer/reader inventory for V71 identity, permission, memory, plan, evaluation, budget, takeover, coordination and autonomous-run records.
- Correct onboarding to use canonical identity construction and fingerprinting; test restart hydration against onboarding-created rows.
- Establish a single versioned persistence adapter with transaction, idempotency, uniqueness, concurrency and audit contracts.
- Review full migration history and live Supabase RLS/constraints with read-only schema inspection; do not assume migration snippets describe current live policy.
- Define cache invalidation and cross-process convergence for web, worker and any external runtime.
- Inventory all credential and identifier generators before selecting final KDO prefix variants; preserve legacy aliases and existing immutable IDs.
- Add integration tests for real DB write → restart → hydration, concurrent creation, cross-workspace denial, RLS, stale-cache revocation and secure credential rotation.

**Decision status:** Gate 0 remains **PARTIALLY COMPLETE**. No live DB query, migration, code edit, test execution, credential rotation, deployment or production action occurred during this audit.


## 19. Expanded V71 workforce writer, migration and recovery audit

This focused pass inspected V71.3–V71.10 and V71.17–V71.18 service implementations, migrations 088–095, 097 and 100, plus the V71.21 atomic budget-reservation migration. It extends—but does not replace—the earlier selected-file audit. The repository search is not a guaranteed exhaustive grep, and live database state remains unverified.

### Mutation and persistence matrix

| Domain | Observed mutation/persistence path | Current convergence assessment |
|---|---|---|
| V71.3 memory | `remember` and `revoke` update the process-local `_memories` dictionary | V71.18 can hydrate `agent_memories`; no matching general durable writer found in the inspected registry |
| V71.4 planning | `create_plan`, `assess` and `cancel` update process-local `_plans` | V71.18 hydrates `agent_plans`; write-through not established |
| V71.5 evaluation | Evaluation creation updates process-local `_evaluations` | V71.18 hydrates `agent_evaluations`; write-through not established |
| V71.6 budgets | Create, usage recording and status changes update process-local `_budgets` | Separate durable reservation facade exists; it does not make all budget definition/status mutations durable |
| V71.7 takeover | Create, claim, resolve and cancel update process-local `_cases` | V71.18 hydrates `agent_human_takeover_cases`; durable human transition writer not established |
| V71.8 coordination | Create, assess, complete and cancel update process-local `_requests` | V71.18 hydrates `agent_coordination_requests`; durable transition writer not established |
| V71.9 autonomous operations | Run creation and state replacement update process-local `_runs` | V71.10 separately persists runs; integration must ensure the in-memory registry cannot be mistaken for authoritative durable state |
| V71.10 durable runtime | Inserts `agent_autonomous_runs`; uses RPCs for claim, lease renewal, finalization and uncertain-run fencing | Dedicated durable lifecycle path; must be reconciled with V71.18 hydration and the execution ledger |
| V71.17 execution | Claims/reads durable run state, validates governance manifest and delegates through existing governed workflow runtime | No separate general-purpose durable executor or authorization plane should be introduced |
| V71.18 persistence | Reads nine tables by `workspace_id`, reconstructs objects and validates fingerprints | Hydration-only; does not write ordinary mutations or include budget reservations |
| V71.21 budget reservations | SQL RPC facade reserves/finalizes/recoveries against `agent_budget_reservations` and aggregate budget counters | Separate durable ledger; absent from V71.18.TABLES and its hydration report |

### Migration findings

- Migrations 090–095 create memory, planning, evaluation, budget, takeover and coordination tables with workspace columns and lookup indexes. Their inspected DDL does not define RLS policies or general-purpose mutation RPCs.
- Migrations 090–095 generally use globally unique record IDs as primary keys while carrying `workspace_id` as a separate column. Unlike V71.1's composite identity key, those tables do not consistently enforce workspace-scoped record identity at the primary-key level. The implications for tenant-safe uniqueness and foreign-key design require explicit review.
- The inspected 090–095 DDL does not consistently declare foreign keys from memory/plans/evaluations/budgets/takeover/coordination records to the corresponding workspace and agent records. This is a statement about these files only.
- Migration 097 adds run attempt/lease/reconciliation fields and SQL functions for claim, renew, finalize and uncertain-run marking. The functions revoke PUBLIC execution and grant `service_role`; the claim/renew/finalize functions use `SECURITY DEFINER`. Actual deployed ownership, grants, RLS interaction and transaction behavior were not queried.
- Migration 100 adds workspace-oriented hydration indexes; it does not create write-through adapters.
- Migration 111 adds reserved budget counters, the `agent_budget_reservations` table, a unique `(workspace_id, budget_id, run_id)` constraint and SQL reservation/finalization/recovery functions. The reservation table references `agent_budgets` by budget ID, but the inspected migration does not declare RLS policies. Expired reservations are marked `uncertain`, not automatically released; this is a safety-preserving state that requires reconciliation.
- Migration 111's reservation state is not part of V71.18's `TABLES` list or hydration report. Therefore a clean V71.18 report cannot, by itself, establish complete budget-reservation recovery.

### Important cross-domain risks

1. **Memory/planning/evaluation are advisory data, not authority.** Their fingerprints detect record modification but are not signatures, credentials or authorization grants.
2. **Budget definition and budget reservation are separate state.** The in-memory registry's usage/status methods are not equivalent to the distributed atomic reservation ledger. Both must converge without double-counting or silently releasing uncertain reservations.
3. **Run recovery and side-effect recovery are separate.** V71.10 correctly treats an expired RUNNING lease as uncertain/reconciliation-required rather than blindly reclaiming it. V71.17 delegates consequential work to existing governed workflow/Action Kernel paths. Keep that invariant.
4. **Hydration readiness is currently too narrow as a platform-level claim.** V71.18 checks its nine tables, but not the separate budget reservation ledger, worker lease ownership, execution ledger/outbox outcome or cross-process cache freshness.
5. **Workspace filters are necessary but not sufficient.** Verify actual RLS, role grants, composite tenant constraints, foreign keys and service-role boundaries against the deployed database. Do not infer isolation solely from application query filters.
6. **Global primary keys with workspace columns need deliberate treatment.** Define whether identifiers are globally unique or workspace-scoped; enforce that decision consistently across records, idempotency keys and foreign-key references.
7. **Atomicity spans multiple subsystems.** Where a single SQL transaction cannot include an external workflow side effect, define an auditable state machine/saga with idempotency and reconciliation rather than pretending the entire action is one transaction.

### Required convergence and acceptance tests

- For each of the nine V71.18 table families: create/update/revoke/transition → durable read → process restart → hydration → exact object/fingerprint equality.
- Verify that a persistence failure is surfaced and does not leave a false success response or an authoritative-looking in-memory-only mutation.
- Run concurrent same-ID and same-workspace creation/update tests across independent processes; verify unique constraints, optimistic concurrency and idempotent retries.
- Verify cross-workspace reads/writes are denied by both application predicates and live RLS/DB privileges.
- Test onboarding-created identities through canonical fingerprint validation and restart hydration; separately verify that declared capabilities never create grants implicitly.
- Exercise V71.10 concurrent claims, lease renewal, stale worker fencing, finalization, expired lease handling and duplicate RPC calls against a real staging database.
- Exercise V71.21 concurrent reservations, idempotent reservation retries, claim-token mismatch, commit/release, expired-to-uncertain recovery, and reconciliation against run/execution-ledger outcomes.
- Verify that no external side effect is repeated merely because a worker lease or budget reservation expired.
- Verify cache invalidation/revocation visibility across web and worker processes and after a restart.
- Include the reservation ledger and execution-ledger/outbox reconciliation in the definition of restart-safe status.

**Decision status:** Gate 0 remains **PARTIALLY COMPLETE**. This is source and migration inspection only. No live Supabase schema/RLS query, RPC invocation, data write, migration, code change, test execution, credential operation, deployment or production action occurred.


## 20. V71 method-level mutation and durable-boundary inventory

### Mutation inventory from the proposal branch

| Domain | In-memory mutation entry points observed | Separate durable path observed | Audit interpretation |
|---|---|---|---|
| V71.3 memory | `remember`, `revoke` | V71.18 hydration only | No general write-through in the inspected registry |
| V71.4 planning | `create_plan`, `assess`, `cancel` | V71.18 hydration only | Plan lifecycle remains process-local at the registry boundary |
| V71.5 evaluation | `evaluate` | V71.18 hydration only | Evidence record; never authority |
| V71.6 budget definition/usage | `create`, `record_usage`, `set_status` | Separate V71.21 reservation RPC facade | Definition/usage projection and reservation ledger are distinct |
| V71.7 takeover | `create`, `claim`, `resolve`, `cancel` | V71.18 hydration only | Human control transitions need durable CAS/version semantics |
| V71.8 coordination | `create`, `assess`, `complete`, `cancel` | V71.18 hydration only | Coordination lifecycle remains process-local at registry boundary |
| V71.9 autonomous operations | `create`, admission/execution, pause-for-takeover, `cancel` | V71.10 durable runtime facade | Preserve V71.10 as owner of durable run/lease fencing |

The inventory is based on the inspected registry source and method bodies. It is not a repository-wide proof that no other caller or writer exists. The next implementation-readiness pass must trace all callers, direct database writes, RPC invocations and migration consumers for these entities.

### Durable boundary details

- V71.10 exposes durable `persist`, `get`, `claim`, `renew`, `finalize` and uncertain-run marking. Its durable path uses direct table insert/select for run creation/read and database RPCs for lease and terminal transitions. Its no-database test path mutates the in-memory operations registry and is explicitly non-authoritative.
- V71.6's `DurableAgentBudgetReservations` calls `reserve_agent_budget`, `finalize_agent_budget_reservation` and `recover_agent_budget_reservations` RPCs. These are not equivalent to the V71.6 registry's `record_usage` method.
- Migration 111 defines `agent_budget_reservations`, a primary key on `reservation_id`, a unique workspace/budget/run tuple, and a foreign key from `budget_id` to `agent_budgets(budget_id)`. The reservation and finalization functions lock relevant rows while changing reserved/used counters.
- Migration 112 updates expired-reservation recovery: expired reservations become `uncertain`; capacity is conservatively transferred from reserved to used pending reconciliation. Do not automatically release uncertain capacity or retry the associated external action.
- These migration observations are source-only. Actual deployed function ownership, grants, RLS, policy interaction, locks under contention and transactional behavior remain unverified.

### Required next trace

1. Trace every caller of each mutation method above and classify whether it is request-path, worker-path, startup/hydration, test-only or administrative.
2. Find every direct writer to the nine V71.18 tables and to `agent_budget_reservations`; identify bypasses around registry validation/fingerprinting.
3. Trace the exact orchestration sequence linking V71.9 run creation/admission, V71.10 durable persistence/lease, V71.6 reservation, Action Kernel/outbox execution receipt, and V71.21 finalization/recovery.
4. Verify the run and reservation state machines against the same workspace, run ID, idempotency key and execution evidence without creating an unsafe cross-system transaction assumption.
5. Add independent-process tests for stale cache, concurrent transitions, duplicate requests, lease expiry, reservation uncertainty and recovery after external side effects.

**Finding:** the architecture needs a shared durable mutation contract plus specialized run-lease and budget-reservation transactions. A single generic CRUD wrapper would be unsafe because it could bypass V71.10 fencing or V71.21 atomic accounting.

**Status:** source-level mutation inventory expanded; caller/writer trace and live database verification remain outstanding. Gate 0 remains **PARTIALLY COMPLETE**. No runtime code, migration, test execution, live database operation, credential change, deployment or production action occurred.
