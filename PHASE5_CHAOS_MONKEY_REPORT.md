# Phase 5 — Chaos Monkey / Adversarial Production Validation

Date: 2026-08-30
Release: v2.22.0

## Objective
Attack trust boundaries and failure paths rather than validating only happy-path behavior.

## Findings and fixes

### P0 — Legacy authentication door remained in `api/v1.py`
The legacy dependency accepted `?token=` credentials and also had parallel app-secret / owner-PSID authentication logic. This created a second authentication model beside canonical `SpeciesAuth`.

**Fix:** `require_auth` now delegates to `SpeciesAuth` and accepts header credentials only. Query-string API credentials are rejected. Legacy APP_SECRET/owner PSID are no longer accepted as API credentials through this path.

### P0 — Webhook verification could fail open
When the webhook secret was absent, `_verify_webhook_signature()` returned success. A query parameter could also act as a secret fallback.

**Fix:** missing webhook secret now rejects the request; only the signed `X-Hub-Signature-256` header is accepted.

### P0 — Authenticated JWT could silently fall into the default tenant
A JWT that had no workspace membership/ownership could be assigned the configured/default workspace. That is a cross-tenant authorization hazard.

**Fix:** workspace resolution now fails closed when no membership/ownership binding exists. No default tenant is assigned to an authenticated but unbound user.

## Regression gates

- Python compilation: PASS
- Phase 5 adversarial tests: 3 PASS
- Failure matrix: 5 PASS
- Tenant isolation matrix: 4 PASS
- Full repository suite: **38 PASS**

## Important remaining live-infrastructure gates

This phase does not claim live production certification. The following require staging infrastructure:

1. Two or more API processes against the same PostgreSQL/Supabase database.
2. Concurrent webhook delivery and duplicate command races.
3. Redis outage/recovery while rate limiting and queues are active.
4. Worker crash after claim and before connector acknowledgement.
5. Cross-tenant JWT/RLS tests against real Supabase policies.
6. Connector timeout, partial success, retry, and idempotency tests.
7. Real WebSocket reconnect/resume with multiple gateway instances.

## Chaos verdict

The previous implementation contained real fail-open/fallback paths that static architecture documentation did not fully describe. They are now closed in the audited paths. The platform is substantially safer, but production certification remains dependent on real infrastructure fault injection.
