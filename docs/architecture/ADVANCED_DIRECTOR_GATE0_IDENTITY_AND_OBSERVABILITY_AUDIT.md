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
