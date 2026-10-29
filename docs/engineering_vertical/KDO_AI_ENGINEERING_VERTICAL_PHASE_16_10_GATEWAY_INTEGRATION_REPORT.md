# KDO AI Software Engineering Vertical — Phase 16.10 KCPG Gateway Context Integration

## Delivered

- Added explicit KCPG gateway routes for engineering capabilities, task intake, task detail, persisted events, plan submission, and revision-bound plan decisions. The routes are mounted before the general BFF and do not depend on the disabled proxy escape hatch.
- Enforced authenticated workspace validation, `viewer + control.read` for reads, and `operator + control.write` for mutations. Conflicting client-supplied workspace context is rejected.
- Added request validation and bounded query parameters at the gateway.
- Added a shared HMAC-signed gateway context envelope containing timestamp, request ID, workspace ID, actor ID, and actor role. The backend verifies the signature and five-minute freshness and uses the signed actor/workspace for engineering task intake and plan-review attribution.
- Added `KCPG_CONTEXT_SIGNING_SECRET` configuration to KCPG and its Render manifest. The same server-only secret must be set in the Python backend.
- Added gateway route integration tests and backend signed-context/tampering tests.

## Required configuration

1. Configure `PYTHON_CORE_BASE_URL` and `PYTHON_SERVICE_TOKEN` as before. Keep the Python service credential private and out of frontend builds.
2. Generate one cryptographically random secret of at least 32 characters and configure it as `KCPG_CONTEXT_SIGNING_SECRET` in both the KCPG gateway and the Python backend. Do not share it with the browser.
3. Apply backend migrations 138–142 in order and configure `KDO_ENGINEERING_DATABASE_URL` for the backend provider.
4. Deploy the Python backend before enabling the frontend route. If the provider/database is missing, the API returns an explicit error rather than fake tasks or agent activity.
5. Verify with real operator accounts, including two different reviewers, and confirm workspace isolation, event attribution, plan digest/revision enforcement, and independent-reviewer enforcement.

## Validation

- Python engineering vertical suite: **180 passed**. Includes valid signed context attribution and tampered signature rejection.
- JavaScript syntax checks passed for the new gateway routes, route registry, config, Python client, and gateway tests.
- Gateway runtime tests could not be completed in this environment: `npm ci` timed out and runtime dependencies (including Express and jsonwebtoken) were unavailable. Syntax checks are not a substitute for end-to-end gateway tests.
- No live PostgreSQL database, deployed gateway, applied migrations, or production engineering worker was available.

## Scope boundary

This phase connects the frontend-to-KCPG-to-Python transport and signed actor/workspace context. It does not enable dynamic AI planning, isolated build workers, test execution, autonomous repair, or deployment authority. The backend must continue to report these capabilities as unavailable until a governed worker lifecycle is implemented and configured.
