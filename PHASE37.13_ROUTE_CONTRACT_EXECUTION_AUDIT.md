# Phase 37.13 — Route Contract / Execution Wiring Audit

## Scope
Static AST audit of all 311 discovered FastAPI routes under `api/`, `routes/`, and `plugins/`. The audit checks four architectural contracts without importing the application:

1. authentication/authorization signals, including router-level dependencies;
2. service/repository/core/container/database wiring signals;
3. external integration signals;
4. mutation routes that could otherwise be unauthenticated or disconnected.

This is intentionally stronger than a route inventory but remains static evidence. It does **not** prove live DB/RLS, Redis, provider, Docker, or network execution.

## Results

| Contract | Result |
|---|---:|
| Routes audited | **311** |
| Parse errors | **0** |
| Duplicate method/path pairs | **0** |
| Routes with auth signal | **245 / 311** |
| Routes with router-level auth signal | **72 / 311** |
| Routes with service wiring signal | **130 / 311** |
| Routes with database/persistence signal | **113 / 311** |
| Routes with external integration signal | **15 / 311** |
| Mutation routes | **126** |
| Mutations lacking an auth signal | **11** |

## Authentication exceptions

The 11 mutation routes without a static auth signal are reviewed as public/webhook or dynamically authenticated surfaces:

- `/api/v1/diagnostics` — uses the backend-contract dynamic `_auth_dep()` dependency; the static detector intentionally does not execute it.
- `/api/v1/github/webhook` — public webhook surface.
- `/api/v1/growthbridge/applications` — public application intake.
- `/api/v1/growthbridge/contact` — public contact intake.
- `/api/v1/growthbridge/partners/apply` — public partner intake.
- `/api/v1/growthbridge/partnerships` — public partnership intake.
- `/api/v1/growthbridge/talent/apply` — public talent intake.
- `/api/v1/metabolism/webhook/stripe` — provider webhook surface.
- `/api/v1/webhooks/linkedin` — provider webhook surface.
- `/api/v1/webhooks/twitter` — provider webhook surface.
- `/webhook` — inbound webhook/message surface.

These should remain subject to their own webhook signature, validation, rate-limit, or equivalent perimeter controls; public does not mean trusted.

## Remaining implementation gap

`GET /api/v1/notifications` and `POST /api/v1/notifications/{notif_id}/read` still expose compatibility behavior without a durable notification repository/read model. This is the one clear route-level functionality gap found by the audit and should be addressed as a dedicated notification persistence phase rather than fabricated from unrelated data.

`GET /e2e/director-hands` reports delegated handlers using a conservative diagnostic heuristic. It does not bypass Director execution authority.

`POST /api/v1/circulation/check` has a real `check_circulation()` path and a fail-closed unavailable branch; the latter is not evidence of a disconnected route.

## Decision

**ROUTE CONTRACT AUDIT: PASS WITH ONE FUNCTIONAL GAP**

The route surface is mounted and structurally wired. No unauthenticated mutation was found outside documented public/webhook categories or the dynamically authenticated diagnostics route. The notification compatibility surface remains intentionally unresolved until a durable tenant-scoped notification model/repository is introduced.
