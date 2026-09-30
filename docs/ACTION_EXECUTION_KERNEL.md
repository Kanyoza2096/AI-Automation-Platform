# Action Execution Kernel — Phase 54

KDO now has a typed governed execution boundary for business actions.

## Invariant

No AI component, automation rule, scheduler, webhook, or external event should
invoke a consequential business mutation directly. It should produce an
`ActionIntent` and execute through `ActionKernel`.

## Pipeline

`ActionIntent → ActionContract validation → risk/approval gate → durable action_outbox → ExecutionLedger lease/idempotency → handler → receipt → action_outbox final state`

The existing publishing canonical pipeline remains authoritative for publishing
and continues to use the same `ExecutionLedger` / `ExternalActionExecutor`
reliability substrate.

## First action family: CRM

- `crm_add_contact` — medium risk
- `crm_add_deal` — high risk, approval required
- `crm_schedule_followup` — medium risk
- `crm_add_meeting` — medium risk

The CRM plugin is reused; this phase does not create a second CRM implementation.

## Safety properties

- Workspace is mandatory and carried into the intent and execution ledger.
- Idempotency is deterministic and tenant-scoped.
- High-risk actions cannot execute without explicit approval.
- Durable action intent persistence is required before the handler is invoked when
  a database is configured; production continues to fail closed when durable
  persistence is unavailable.
- Provider/domain failures remain failures; ambiguous execution remains uncertain
  and requires reconciliation rather than blind retry.
- Business Automation now propagates governed action statuses instead of marking
  an uncertain or failed action as completed.

## Deliberate limitation

The action outbox has a service-role claim RPC in migration `045`. A dedicated
multi-process action worker and provider-specific reconciliation adapters are the
next reliability increment. This phase therefore establishes the contract and
persistence boundary without falsely claiming that asynchronous recovery is
already fully certified.
