# Phase 41 — Business Event Gateway

Business Automation is now able to receive real business events from external systems through tenant-scoped generic webhooks.

## Contract

1. Workspace administrator creates an event source.
2. KDO returns a one-time webhook secret and ingest path.
3. External system sends `event_name`, business payload, `X-KDO-Webhook-Secret`, and unique `X-KDO-Event-ID`.
4. KDO authenticates the source and checks the event allowlist.
5. A durable receipt is claimed as the idempotency/replay fence.
6. The event is published to the Unified Event Bus with the source workspace.
7. BusinessAutomationEventBridge matches subscribed rules.
8. Consequential work remains governed by BusinessAutomationEngine and the Action Kernel.

The ingress endpoint never executes customer automation inline.

## Current scope

Generic webhook is the only supported external source type in this phase. Native CRM, WhatsApp, website-form, billing, and email provider adapters should use this event contract or be implemented as first-class adapters in later phases; they are not falsely marked live by this gateway.
