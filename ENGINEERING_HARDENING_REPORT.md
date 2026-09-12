# Kanyoza AI Automation Platform — Engineering Hardening Pass

## Scope
This pass inspected the implementation rather than relying on architecture/status documentation. The supplied repository contains 466 Python files, 37 SQL migrations, and 32 test files.

## Baseline
- Existing test suite: 94 passed.
- Initial review identified inconsistent enforcement of already-designed security primitives.

## Fixes applied
1. **Canonical plugin tenancy enforcement**
   - Generic plugin execution now routes through `run_plugin()`.
   - Client-supplied `workspace_id` is no longer authoritative for ordinary principals.
   - `workspace_id` is stripped from plugin params to prevent a second source of tenancy truth.

2. **PDF upload tenancy and durability**
   - Upload workspace is resolved through the canonical plugin workspace boundary.
   - Client filenames are sanitized before being used in storage keys.
   - Unsupported upload purposes are rejected.
   - Storage failure now returns HTTP 503 instead of reporting a successful upload that was not durably stored.

3. **Control-plane authorization**
   - Added `require_species_privileges()` for platform-level operations.
   - Circulation control-plane routes and Code Intelligence routes now require species/platform privileges rather than authentication alone.

4. **Workspace administration RBAC**
   - Added `require_workspace_admin()`.
   - Workspace update/delete and workspace member administration routes now require owner/admin/species authority instead of merely being a workspace member.

5. **Regression coverage**
   - Added tests for control-plane authorization.
   - Existing plugin isolation tests remain green.

## Verification
- Final test suite: **96 passed**.
- Python compilation checks passed for all modified modules.

## Important architectural conclusion
The platform already contains strong primitives for tenancy, governance, idempotency, outbox execution, and reconciliation. The principal weakness was inconsistent enforcement at some HTTP boundaries. The hardening pass moves those boundaries toward a "secure by construction" model.

## Remaining work for a true production 10/10
A 10/10 production assessment cannot honestly be declared from static inspection alone. The next required gate is environment-backed integration testing against the real database/RLS policies, Redis/queue behavior, external connector contracts, concurrency/load tests, and failure-injection tests.


## Phase 7 — Tenant/API and Execution-RPC Hardening

- Removed remaining body/query-selected workspace authority from post creation, user management, and rate-limit configuration paths; these now resolve workspace from canonical request context (with species-only explicit selection).
- Restricted general user create/update/delete to workspace administrators and enforced trusted workspace scope.
- Restricted rate-limit configuration changes to workspace administrators and prevented arbitrary workspace IDs from being persisted.
- Added `migrations/031_execution_rpc_hardening.sql`: durable execution RPCs are executable only by `service_role`; lifecycle status and lease inputs are validated at the database boundary; completion clears execution leases; lease duration is bounded.
- Regression suite remains green: 96 tests passed.

### Remaining release-gate work

A true production sign-off still requires environment-backed tests against the real Supabase schema/RLS, connector credentials in a staging environment, concurrent worker/failover testing, load testing, migration rehearsal, and security testing of every externally reachable route.


## Phase 8 — Adversarial Tenant Boundary Hardening

- Closed the species workspace fail-open path: if the authoritative workspace registry is unavailable, a species-selected `X-Workspace-ID` now fails with HTTP 503 instead of becoming trusted request context.
- Hardened organism dashboard reads so workspace selectors are passed through the canonical tenant guard and unbound identities cannot fall back to a synthetic `default` workspace.
- Hardened timeline correlation/decision reads and outcome writes with workspace authorization, preventing cross-tenant decision/event inspection or outcome attachment.
- Hardened metabolism status/checkout workspace resolution and restricted checkout to workspace administrators. Species plan changes now validate that the selected workspace exists and is active.
- Removed duplicate `UserCreateRequest` / `UserUpdateRequest` model definitions that were silently shadowing richer schemas and could cause runtime/API contract inconsistencies.
- Prevented non-owner workspace administrators from granting the `owner` role.
- Hardened feature-flag mutation to use canonical workspace resolution and workspace-admin authorization.

### Current verification
- Python compilation: passed.
- Full pytest suite: **96 passed**.
- No test regressions introduced by Phase 8.
