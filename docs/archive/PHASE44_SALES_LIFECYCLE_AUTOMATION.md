# Phase 44 — First Complete Sales Lifecycle

## Purpose

V59 turns the website lead path into a chained business process rather than an isolated CRM write:

`website lead -> lead.created -> governed CRM contact -> crm.contact.created -> governed follow-up task`

The chain is event-driven, tenant-scoped, idempotent, and remains inside the existing Action Kernel for consequential CRM writes.

## Key changes

- Declarative rule parameters now support bounded `{{event.field}}` interpolation, including nested fields.
- Exact placeholders preserve source value types; there is no expression evaluation or executable template language.
- Successful `crm_add_contact` publishes the bounded `crm.contact.created` lifecycle event only after the governed CRM action succeeds.
- Lifecycle events carry the originating event as `parent_event_id` and preserve workspace scope.
- If the lifecycle event cannot be published after a successful CRM write, the automation execution is reported as `uncertain` with reconciliation required rather than falsely marked complete.
- Added `sales.new-lead-followup` starter template. It schedules the first CRM follow-up using the lifecycle event's generated `follow_up_at` timestamp.
- The first-party website lead adapter therefore has a concrete next action without requiring the customer to manually wire a second rule.

## Governance boundary

The lifecycle event is an internal domain event, not an external side effect. The CRM contact creation and follow-up scheduling remain governed CRM actions executed through the Action Kernel. No arbitrary code, URL, provider credential, or shell command can be supplied through rule parameters.

## Verification

- Targeted lifecycle/catalog/bridge tests: **18 passed**
- Full backend suite: **365 passed, 1 Pydantic V1 deprecation warning**
- Live staging: **not certified**

## Remaining production milestone

The next step is to certify this chain against a real Supabase-backed workspace and a real website form, then add controlled customer communication (email/WhatsApp) only after the contact/follow-up state machine is observable and reconciliable.
