# Phase 39 — Event-Driven Business Automation

## Status
Implemented in v8 candidate.

## Control flow

`UnifiedEventBus → BusinessAutomationEventBridge → explicit trigger match → rule condition → durable automation execution`

The EventBus envelope now exposes trusted `workspace_id`, `brand_id`, `decision_id`, `correlation_id`, `parent_event_id`, and `event_id` to subscribers. Business Automation uses the trusted workspace identity rather than a rule parameter as authorization authority.

## Safety boundaries

- Existing rules are **not** automatically event-triggered.
- A rule must explicitly declare `parameters.trigger_events`.
- Trigger values can be event names or `*` for an intentionally broad subscription.
- Events without a trusted workspace are ignored.
- Platform-scoped events are ignored by workspace automation.
- Automation-generated events are excluded to avoid self-triggering loops.
- High/critical rules still require approval and therefore cannot silently become autonomous merely by being event-triggered.
- Existing Condition Engine, Council, Execution Ledger, and ExternalActionExecutor boundaries remain authoritative.
- Event execution uses deterministic idempotency including rule ID, event ID, event name, and rule version.

## Operational limitation

The current EventBus subscriber path is live/reactive, not yet a fully durable event-consumer queue. The EventBus persists events when configured, but a process failure between event persistence and subscriber execution still requires a replay/claim consumer to guarantee eventual automation processing.

That is the next hardening gate before claiming durable event-driven delivery semantics.
