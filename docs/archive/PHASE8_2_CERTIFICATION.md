# KDO Phase 8.2 — Tenant / Workspace Isolation Hardening

## Status
CERTIFIED — architecture / integrity scope

## Scope
A reusable fail-closed isolation boundary now binds every registered resource to
exactly one tenant and workspace. Access requires an exact scope match and an
intact integrity fingerprint. Resources cannot be silently rebound to another
scope.

## Controls
- Durable tenant + workspace binding in SQLite.
- Exact tenant and workspace access checks.
- Unknown resources fail closed.
- Cross-tenant and cross-workspace access fail closed.
- Existing resource scope cannot be rebound.
- Tampered scope records are detected before authorization of access to the record.
- Restart-safe rehydration.
- Scope listing is tenant/workspace filtered.
- No authorization, execution, provider, or Action Kernel authority.

## Validation
- Phase 8.2 dedicated tests: 5/5 passed.
- Phase 8.1 + 8.2 + runtime architecture: 13/13 passed.
- compileall: PASS.
- Architecture contracts: 6/7 passed.

## Known environment limitation
The single architecture-contract failure is the pre-existing Docker CI extraction-layout
check expecting `.github/workflows/docker-build.yml` at `/mnt/data/kdo_phase8/.github/...`.
The current extracted project is under `work802/`. This is packaging/environment layout,
not a Phase 8.2 behavioral failure.

## Authority invariant
`authority=none`
`can_authorize=false`
`can_execute=false`
`can_call_provider=false`
`can_invoke_action_kernel=false`

Isolation establishes scope; it does not decide whether an operation is permitted or
execute any consequential action. Consequential operations continue through the
canonical KDO governance and execution chain.
