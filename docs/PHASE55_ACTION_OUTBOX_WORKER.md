# Phase 55 — Action Outbox Worker

KDO now has a separate asynchronous execution plane for governed business actions.

## Contract

`ActionKernel.enqueue()` is an acceptance boundary: it validates the typed action,
checks risk/approval requirements, and durably records the intent in
`action_outbox` before returning acceptance. It does not perform the external side
effect.

`ActionOutboxWorker` claims durable rows with `FOR UPDATE SKIP LOCKED` through the
service-role `claim_action_outbox()` RPC and executes each claimed intent through
the same `ActionKernel -> ExecutionLedger -> ExternalActionExecutor` boundary.

This gives the platform:

- multi-process claiming without local queue ownership;
- workspace/idempotency fencing at the durable boundary;
- stale-worker recovery via leases;
- approval state persisted separately from execution;
- conservative uncertainty on worker/provider failures;
- one execution path for inline and asynchronous governed actions.

## Failure invariant

A worker crash after a provider may have received a request is **not** interpreted
as a safe retry. The execution ledger and provider reconciliation remain the
source of truth. An ambiguous execution is surfaced as `reconciliation_required`.

## Runtime

The production `workers` process now runs both:

1. business automation dispatch; and
2. the durable action outbox worker.

The API/core processes do not own action-outbox execution.

## Approval

High/critical or explicitly irreversible actions can be accepted with
`approval_required`. `ActionKernel.approve()` transitions the durable intent to
`pending`; only the worker performs the side effect afterward.

## Current scope

The first registered family is CRM:

- `crm_add_contact`
- `crm_add_deal`
- `crm_schedule_followup`
- `crm_add_meeting`

The kernel is provider-agnostic so additional action families can reuse the same
execution, governance, idempotency and recovery substrate.
