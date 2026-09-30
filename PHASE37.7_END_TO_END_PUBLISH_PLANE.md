# Phase 37.7 — End-to-End Publish Plane

## Objective

Ensure manual, scheduled, and automated publish triggers converge on one canonical Facebook/social publishing path and can progress from accepted intent to verified external publication without an avoidable governance/credential wiring dead-end.

## Canonical lifecycle

`Trigger -> workspace/brand -> bounded DecisionGate authorization -> Guardian -> Metabolism -> Council (when required) -> durable publish outbox -> outbox worker -> execution ledger -> tenant-specific connector/plugin -> provider -> verified receipt -> job PUBLISHED -> learning/memory`

## Trigger modes

### Manual

`POST /api/v1/posts` with `state=publish` is a trusted authenticated user intent. If no decision is supplied, only `trivial`/`low` risk requests receive a bounded `DIRECTOR_AUTO` DecisionGate record. Medium/high/critical requests still require an explicit decision.

### Scheduled

`POST /api/v1/posts` with a future `scheduled_for` creates a durable `scheduled_posts` row with `status=queued`. The scheduler claims the row and invokes the same PostPublishWorkflow and canonical publish path.

### Automated

Brand scheduler/workflow triggers receive the same bounded low-risk authorization and converge on the same durable outbox and worker path.

## Facebook credential correctness

The canonical pipeline resolves the Brand and passes it to the Facebook plugin. The Facebook plugin's configured SocialAccountRepository therefore selects credentials by `brand_id + facebook`, rather than silently relying on a global/default account in production.

The Brand workspace must equal the publish workspace. Mismatch fails closed.

## Completion semantics

A durable publish response is `queued`, not `published`. The outbox worker is the component that can transition a job to `PUBLISHED`, and only after the execution ledger records a successful provider result and the durable outbox finalization succeeds.

Provider transport exceptions remain `uncertain` and require reconciliation; they are never silently converted into a retryable normal failure that could duplicate a post.

## Validation

- 218 tests passed
- 1 existing deprecation warning
- Python compilation passed
- Production artifact gate passed
- 39 SQL migrations present
- Real Facebook/DB/Redis provider execution was not claimed in this environment; staging credentials and infrastructure remain required.
