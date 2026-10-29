# Business Automation — Full Durable Stage

## Responsibility boundary

Business Automation owns **triggering and orchestration**. It does not become a second decision engine and it does not call arbitrary external APIs.

- Condition Engine: decision authority.
- Council/governance: authorization for consequential decisions.
- Execution Ledger + ExternalActionExecutor: side-effect authority, idempotency and uncertainty handling.
- Supabase: durable rules and execution history.
- Workers: scheduled dispatch.

## Durable behavior

Rules support immediate event evaluation plus `schedule_at` / recurring `interval_seconds` execution. Production application modes fail closed when Supabase is unavailable.

Scheduled rules are claimed through a PostgreSQL `FOR UPDATE SKIP LOCKED` RPC. Execution intents are reserved before action execution with a tenant-scoped unique idempotency key.

## Current registered actions

- `log` — internal audit/logging only.
- `set_context` — returns proposed state; it does not silently mutate durable state.
- `content_intelligence` — research/intelligence only.
- `condition_decision` — submits an observation to the Condition Engine and can optionally execute the resulting governed plan through its existing execution authority.

No generic HTTP/code execution action is provided. New business actions must be explicit adapters and should use `ExternalActionExecutor` for externally visible side effects.

## Operational requirement

Applying migration `039_business_automation.sql` is mandatory before enabling scheduled business automation in production. Supabase/RLS and service-role RPC execution must be verified in staging.
