# KDO Lean Unified Environment Strategy

**Status:** proposed implementation baseline  
**Scope:** KDO backend, KCPG Gateway, and the Internal Command Center  
**Branch targets:** backend `staging`; KCPG Gateway `KDO`; Internal Command Center `KDO`

## 1. Operating model

KDO uses a lean, shared-infrastructure approach where it is operationally safe. Building and validating software may happen against the same non-customer environment and shared service footprint. The strategy does not require a second full copy of every service, database, queue, or deployment solely to create a nominal testing tier.

A shared footprint is not permission to mix identities, records, or execution authority. Use logical isolation inside the footprint.

## 2. Data classification

The current development/test environment must contain no customer data and no sensitive personal data. Use synthetic, generated, or explicitly approved non-sensitive operational data.

Treat credentials, signing keys, provider tokens, session cookies, and private configuration as sensitive even when customer records are absent. Keep secrets server-side, never commit them, and never expose service-role credentials to browser builds.

Operational records such as jobs, mission events, evidence, audit entries, and deployment history may be non-customer data but are still integrity-sensitive. Do not purge them casually; use explicit retention, archival, and recovery procedures.

## 3. Logical isolation

- Workspace/tenant IDs and test identities must be distinct from any future customer identities.
- Test jobs, queues, mission runs, artifacts, and provider connections must be identifiable and filterable.
- Test credentials and provider integrations must be non-production credentials with the minimum required scopes.
- External publishing, payments, messaging, and irreversible actions must be disabled, mocked, or explicitly allowlisted in test flows.
- Database migrations must be reviewed for scope, ordering, idempotency, rollback/recovery, and target project before execution.
- Background workers must not consume test work as production work or vice versa without an explicit routing contract.

## 4. Environment labels are not infrastructure instances

A deployment label describes runtime intent; it does not prove that a separate server or database exists. The repository branch is also not proof of the runtime environment.

The branch targets are:

| Component | Authoritative development target |
|---|---|
| KDO backend (`AI-Automation-Platform`) | `staging` |
| KCPG Gateway (`KCPG-Gateway`) | `KDO` |
| Internal Command Center (`KDO-Control-plane-`) | `KDO` |

These are source-control targets only. Do not infer a Supabase project, Render service, Cloudflare deployment, or production/customer-facing status from a branch name. Verify deployment wiring and resource identifiers independently before any migration, data mutation, or release.

## 5. Release and change discipline

Use the incremental sequence:

1. Build a small, reviewable change.
2. Run focused tests and relevant repository CI.
3. Review security, tenancy, data scope, and side-effect boundaries.
4. Merge only when complete and authorized.
5. Deploy only to the verified intended target.
6. Verify the resulting runtime and data state.
7. Continue to the next increment.

Passing CI does not itself authorize a merge, migration, deployment, or external action. A deployment must not be called production merely because a repository or environment variable uses the word `production`.

## 6. Required safeguards

- Fail closed on unknown environment labels.
- Keep production-only safeguards enabled whenever the runtime is classified as production.
- Keep test-only switches explicit and unavailable to untrusted callers.
- Preserve auditability for state-changing operations.
- Back up important operational state and verify recovery paths.
- Prefer logical isolation and bounded retention over duplicating entire infrastructure stacks.

## 7. Current limitation

This document establishes the operating policy; it does not certify that the currently connected hosting, database, queue, or deployment resources have been mapped correctly. That inventory must be verified from authoritative deployment configuration before changing any live resource.
