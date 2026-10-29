# Phase 38.5 — Durable Notification Side Effects

## Objective

Move Business Automation notifications behind the same durable execution boundary used by consequential content mutations and external connectors.

## Implemented

- `notify_operator` now requires a workspace and automation idempotency key.
- Notification delivery is executed through `ExecutionLedger` + `ExternalActionExecutor`.
- The ledger creates the durable intent before calling Slack, Discord, or SendGrid.
- Provider/network exceptions remain `uncertain` and require reconciliation.
- Multi-channel partial delivery is explicitly classified as `uncertain`, preventing an unsafe blind retry.
- Fully delivered notifications are `succeeded`; no delivered channels are `failed`.
- Business Automation therefore no longer records a notification as ordinary completion merely because a direct notifier call returned.

## Boundary

Business Automation remains responsible for rule evaluation and orchestration. `ExternalActionExecutor` owns the side-effect execution contract. The notifier remains responsible for provider-specific transport.

The platform does **not** claim exactly-once delivery. It provides durable idempotency and an evidence boundary; provider-specific reconciliation is still required for ambiguous delivery.

## Verification

- Full automated suite: **267 passed, 1 warning**.
- Added notification-specific coverage for governed execution and partial delivery.

## Remaining production gate

Real staging verification is still required for durable ledger RPCs, Slack/Discord/SendGrid credentials, provider behavior, and reconciliation workflows.
