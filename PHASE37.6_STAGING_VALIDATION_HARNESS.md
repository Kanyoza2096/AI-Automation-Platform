# Phase 37.6 — Staging Validation Harness

## Purpose

Provide a deterministic evidence boundary between static release certification and real staging validation.

The harness does **not** connect to infrastructure itself. Staging adapters inject authoritative probes for:

1. Postgres/RLS
2. Redis/distributed coordination
3. Execution ownership/fencing
4. Webhook idempotency
5. Provider adapter behavior
6. Organism lifecycle

Missing or unknown evidence is `UNOBSERVABLE`, never healthy.

## Failure injection

Controlled chaos scenarios are injected as callables and must report the expected safe behavior. The harness never treats an exception as a successful chaos result.

Recommended staging scenarios:

- Redis loss during a distributed-critical operation: refuse unsafe local fallback.
- Execution lease loss: stale worker cannot complete.
- Webhook handler failure: delivery remains retryable.
- Provider timeout after request dispatch: external result remains uncertain.
- Database/RLS denial: cross-tenant operation is rejected.
- Organism dependency degradation: cycle reports degraded/blocked rather than inventing health.

## Safety boundary

This harness is validation-only. It cannot deploy, grant credentials, alter production state, execute provider actions, or override Guardian/DecisionGate.
