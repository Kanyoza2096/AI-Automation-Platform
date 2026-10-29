# Phase 7.36 — Workforce Coordination Policy Governance & Activation

## Status
CERTIFIED — architecture/integrity scope.

## Scope
Phase 7.36 introduces governed lifecycle management for adaptive workforce coordination policies:

PROPOSED → REVIEWED → APPROVED → ACTIVE → SUPERSEDED / ROLLED_BACK

The governance layer is coordination-policy control-plane evidence only. It does not authorize, execute, provision, invoke providers, invoke the Action Kernel, or mutate historical outcomes.

## Controls
- Workspace/tenant fencing.
- Versioned policy identity and immutable policy fingerprint binding.
- Separation of duties between proposer, reviewer, and approver.
- Explicit activation step.
- Single ACTIVE policy per workspace/policy identity.
- Durable SQLite policy-change records.
- Append-only audit projection.
- Exact active-binding check on policy ID, version, and fingerprint.
- Tamper detection for policy changes and audit events.
- Supersede/rollback lifecycle states.
- No authorization/execution/provider/Kernel authority.

## Validation
- Dedicated 7.36 governance tests: 9/9 PASS.
- Adaptive-planning + coordination-feedback regression: 13/13 PASS.
- Qualification/coordination/runtime focused suite: 42/42 PASS.
- Python compileall: PASS.
- Architecture contracts + runtime + governance: 13/14 PASS.

## Known environment limitation
The single architecture-contract failure is the pre-existing extraction-layout test
`test_docker_ci_builds_canonical_root_dockerfile`, which expects
`/mnt/data/kdo_phase7/.github/workflows/docker-build.yml` while this project is extracted under
`/mnt/data/kdo_phase7/work735`. No 7.36 implementation failure was observed.

## Authority invariants
`authority = none`
`advisory_only = true`
`authority_granted = false`
`can_authorize = false`
`can_execute = false`
`can_call_provider = false`
`can_invoke_action_kernel = false`

## Artifact
Phase 7.36 full project package contains the complete patched project and certification materials.
