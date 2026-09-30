# Phase 95 — Director Runtime Alignment

## Findings

The Director screen exposed three distinct states that must not be conflated:

1. `1/37 responding`: `/director/health` is intentionally unauthenticated inside the Director sub-app, while the remaining read surfaces require the gateway's Python service credential. If only health responds, the runtime deployment must verify that KCPG `PYTHON_SERVICE_TOKEN` exactly matches the backend `PYTHON_SERVICE_TOKEN` (or accepted `MASTER_API_TOKEN`). No code path should weaken Director authentication to make coverage green.
2. `FAILED director.observe — required control scope is not present`: the active OWNER role is correctly recognized, but the session shown has 13 scopes and does not include the dedicated `director.write` scope. Director mutations intentionally require both `control.write` and `director.write`. The gateway default scope set now includes `director.write`; an existing JWT/session must be re-issued after deployment or explicitly configured with that scope.
3. `ORGANISM LINK UNKNOWN / SERVICES 0/2`: this remains an independent runtime health condition. It is not safe to rewrite it in the UI. The actual `/system/health` payload must identify the two registered services and their observed status before any further code change.

## Changes

- Added `director.write` to the canonical KCPG control-plane default scopes.
- Updated `.env.example` accordingly.
- Director Contract Coverage now preserves HTTP failure evidence instead of collapsing every failed request into `UNAVAILABLE`; e.g. `HTTP 401`, `HTTP 403`, or `HTTP 502` remains visible to operators.
- Existing `systemHealth` evidence alignment and deterministic event-summary changes remain intact.

## Security posture

No role-to-scope bypass was introduced. OWNER remains the highest human workspace role, but specialized scopes remain explicit. Director mutations continue through KCPG governed command contracts.

## Verification

- Backend: 354 passed, 1 warning.
- Gateway command/role/Phase-94 tests: 11 passed.
- Gateway JS syntax checks: passed.
- Backend Python compilation: passed.

## Deployment action

After deploying the gateway configuration, refresh/re-authenticate the control-plane session so the issued JWT contains `director.write`. Separately ensure the same strong `PYTHON_SERVICE_TOKEN` is configured on both KCPG and the Python backend.
