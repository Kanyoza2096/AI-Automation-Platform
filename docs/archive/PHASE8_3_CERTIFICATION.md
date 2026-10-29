# KDO Phase 8.3 — Identity & Access Hardening

## Status
CERTIFIED — architecture / integrity scope

## Scope
A durable identity and session evidence boundary now establishes the requesting
principal, exact tenant/workspace scope, authentication assurance, identity state,
and session validity. This layer is provenance and access-context infrastructure;
it does not make authorization decisions or execute consequential operations.

## Controls
- Durable principal identity records in SQLite.
- Exact tenant + workspace identity binding.
- Identity scope rebinding denied.
- Identity states ACTIVE / SUSPENDED / REVOKED.
- Authentication assurance recorded as evidence.
- Role/capability fingerprints recorded without granting authority.
- Durable access sessions bound to the identity and exact scope.
- Session expiry and explicit revocation fail closed.
- Unknown, tampered, mismatched, or inactive identities fail closed.
- Identity and session fingerprints detect unauthorized record mutation.
- Restart-safe identity/session rehydration.
- No authorization, execution, provider, or Action Kernel authority.

## Validation
- Phase 8.3 dedicated tests: 5/5 passed.
- Phase 8.1 + 8.2 + 8.3 + runtime architecture: 18/18 passed.
- compileall: PASS.
- Architecture contracts: 6/7 expected pass; one known Docker extraction-layout limitation.

## Known environment limitation
The architecture-contract Docker check expects `.github/workflows/docker-build.yml`
at `/mnt/data/kdo_phase8/.github/...`; the extracted working project is under
`work803/`. This is packaging/environment layout, not an 8.3 behavioral failure.

## Authority invariant
`authority=none`
`can_authorize=false`
`can_execute=false`
`can_call_provider=false`
`can_invoke_action_kernel=false`

Identity establishes provenance and session validity. Authorization remains a
separate canonical control-plane decision and consequential operations continue
through Governance -> Authorization -> Action Kernel -> Connector Fabric ->
Provider -> Receipt -> Verification -> Reconciliation.
