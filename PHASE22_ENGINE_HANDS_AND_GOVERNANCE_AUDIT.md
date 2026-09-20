# Phase 22 — Engine Hands & Governance Audit

## Scope
Traced Director, Council, Condition Engine, Meta Engine, Content Engine, publishing orchestration/worker, and event buses from decision through execution and verification.

## Findings fixed

### 1. Director Council integration bypassed the real publishing hand
`DirectorCouncilIntegration._council_approved_publish()` generated content after Council approval but did not invoke the production `publish_scheduled_content` handler. This could produce content while claiming to perform a publish operation.

**Fix:** preserve the original production handlers before wrapping them with Council authorization and invoke the original handler after approval.

### 2. Director restart integration returned a fabricated success
`_council_approved_restart()` returned `{ok: true, restarted: service}` without actually restarting the service.

**Fix:** delegate to the original `restart_failed_service` production handler after Council approval.

### 3. Director optimize/revival integration duplicated execution logic
The wrappers directly called Content Engine, bypassing the canonical Director capability handlers.

**Fix:** Council now authorizes; the original capability handler performs the action.

### 4. Condition Engine treated unsupported actions as successful passive execution
Unknown actions returned `success=True` with `note=passive`.

**Fix:** unsupported actions fail closed with `unsupported=true` and an explicit executor-unavailable error.

### 5. Council decision cache was insufficiently scoped
Decision cache keys omitted workspace, brand, action parameters, metadata, and risk context. A cached decision could therefore be reused for a materially different proposal.

**Fix:** cache key now includes a SHA-256 digest of the governance context.

## Canonical dataflow after fixes

Observation → Director/Condition/Meta analysis → Council/policy authorization → canonical capability handler → real executor → result → verification → persistence/events → learning.

## Truth rules
- Authorization is not execution.
- Returning from a handler is not proof of success.
- Unsupported capabilities fail closed.
- Recovery success requires post-action health verification.
- Governance cache entries are tenant/context specific.

## Verification
- Python compilation: PASS
- Test suite: 81 passed
- No existing test regressions.

## Environment-dependent gates still excluded
Real provider effects, live database/Redis behavior, Docker runtime, and production credentials remain staging responsibilities.
