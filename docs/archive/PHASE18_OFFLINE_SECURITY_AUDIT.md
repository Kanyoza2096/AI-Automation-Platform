# KDO Phase 18 — Offline Security & Failure-Path Hardening

## Fixes

### 1. Species workspace validation crash path
`RequestContextMiddleware` referenced `supabase_client` before defining it when validating a master/species-selected workspace. This could turn authenticated species requests with `X-Workspace-ID` into a 500. The client is now resolved from the application container before workspace validation.

### 2. Supabase membership acceptance lookup
`SpeciesAuth._resolve_workspace_for_email()` filtered membership rows by `accepted_at`, but the query did not select `accepted_at`. As a result, valid accepted memberships could be treated as absent and incorrectly fall through to owner-email resolution. The query now selects `workspace_id, role, accepted_at`.

## Verification
- Python compilation: PASS
- Automated test suite: 81 passed
- No test regressions

## Remaining environment-dependent certification
Real Supabase/Redis/provider integration, Docker runtime, and deployment certification remain intentionally skipped per project direction and must be run by the operator in staging.
