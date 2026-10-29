# KDO Phase 7.37 — Workforce Coordination Policy Enforcement & Consumption Binding

## Status
**CERTIFIED — architecture/integrity scope**

Phase 7.37 closes the gap between a governance-approved coordination policy and the policy actually consumed by adaptive planning.

## Enforcement boundary
Every governed adaptive-planning assessment must prove:

- exact policy ID
- exact policy version
- exact policy fingerprint
- exact governance change ID
- governance state is `ACTIVE`
- workspace identity matches
- governance record integrity is valid
- durable consumption binding exists
- binding fingerprint is valid

The enforcement layer fails closed for missing, stale, mismatched, superseded/rolled-back, tampered, or cross-workspace policy state.

## Authority boundary
The enforcement layer has:

- `authority = none`
- `can_authorize = false`
- `can_execute = false`
- `can_call_provider = false`
- `can_invoke_action_kernel = false`

It does not mutate the canonical plan, authorize workers, execute actions, invoke providers, or replace governance.

## Implementation
Primary service:
`services/v71_48_workforce_coordination_policy_enforcement.py`

Primary artifact:
`KDO-Phase7.37-Workforce-Coordination-Policy-Enforcement-Patched-Project.zip`

## Validation
- Phase 7.37 dedicated tests: **5/5 passed**
- 7.35 + 7.36 regression: **21/21 passed**
- Combined relevant focused/runtime gate: **53/54 passed**
- Remaining 1 failure: pre-existing Docker CI contract expects `/mnt/data/kdo_phase7/.github/workflows/docker-build.yml` while the extracted project root is `/mnt/data/kdo_phase7/work737`. This is an extraction-layout/environment limitation, not a Phase 7.37 behavior failure.
- `compileall`: **PASS**
- Authority scan: **PASS**

## Exit meaning
Phase 7.37 does not make policy authoritative. It makes policy consumption provable and fail-closed against the authoritative governance record established in Phase 7.36.

Next logical boundary: Phase 7.38 — policy audit/history consumption evidence, unless existing evidence proves the requirement already satisfied and Phase 7 can proceed directly toward exit hardening.
