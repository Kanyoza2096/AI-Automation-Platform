# Phase 42 — Sales Operations Starter Pack

KDO now treats sales automation as a business outcome surface rather than a generic workflow builder.

## Included customer-facing starters

- New lead capture → CRM contact
- Qualified lead → CRM opportunity
- Follow-up due → CRM follow-up
- High-value lead escalation
- Lead follow-up task

Templates are declarative. Installation creates a paused governed rule. Event-triggered templates automatically subscribe the rule to the template trigger; schedule/manual templates do not.

## Authority

Inbound events enter through the Business Event Gateway/EventBus. Business Automation evaluates subscribed rules and conditions. Consequential CRM actions execute through the Action Kernel and retain authorization, idempotency, audit, and approval semantics.

## Not yet claimed

This phase does not claim native Facebook/WhatsApp/website/third-party CRM ingestion. External systems must send an allowed event through a configured event source until provider-specific adapters are certified.
