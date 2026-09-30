# Phase 42 — Cross-Platform AI and Publishing Hardening

## Scope

This phase deliberately broadens the engineering sweep beyond Business Automation.
It hardens two shared platform planes: probabilistic AI access and external publishing.

## AI Gateway convergence

Code Intelligence now uses `LLMGateway.complete_structured()` instead of importing the
Gemini service directly. This makes Code Intelligence subject to the platform's caller
allowlist, provider orchestration, budget/circuit policy, degraded semantics, and
structured-output validation.

The gateway default allowlist now explicitly includes `code_intelligence`.

The legacy Gemini adapter remains in Code Intelligence only for compatibility and is no
longer the primary analysis path.

## Publishing outcome semantics

Connector results now distinguish:

- `failed`: the provider deterministically rejected the request or local configuration
  makes execution impossible; retry/reconciliation is not required merely because the
  provider returned an error.
- `uncertain`: transport/provider ambiguity means the external side effect may have
  happened; retrying blindly can duplicate external state.
- `unknown`: legacy/default connector result where the connector did not declare a
  stronger outcome.

The durable execution executor respects explicit connector outcome declarations.
HTTP 4xx/provider validation failures are therefore terminal failures, while transport
exceptions and 5xx-class responses remain conservative/ambiguous.

## Verification

278 tests pass locally with one pre-existing deprecation warning.

This is local/static verification only. Real provider, Supabase, Redis, and staging
publication evidence remains a certification requirement.
