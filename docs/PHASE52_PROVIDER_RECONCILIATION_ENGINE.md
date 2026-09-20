# Phase 52 — Provider Reconciliation Engine

## Goal

Make asynchronous provider evidence part of the canonical publishing reliability
model without allowing fuzzy matches or eventual-consistency misses to create
false publication or duplicate side effects.

## Implemented

- Provider webhook reconciliation service.
- Exact `(platform, provider object id)` matching against the durable publish
  outbox.
- WhatsApp Cloud API status webhook endpoint.
- Webhook delivery idempotency keyed by provider message/status/timestamp.
- `sent`, `delivered`, `read`, `published`, and `finished` resolve the exact
  uncertain item as provider-accepted/published.
- Provider `failed` resolves the exact item as failed; it is not automatically
  retried.
- Unknown provider states remain uncertain.
- Workspace-scoped lookup is supported where the workspace is known.
- No caption, timestamp, recipient, or fuzzy-content matching is permitted.

## Safety model

A webhook cannot create a new publish intent. It can only reconcile an existing
outbox intent when the provider object ID exactly matches a persisted outbox
receipt. This prevents a webhook for another post from resolving the wrong
workspace's execution.

WhatsApp's official Cloud API documentation states that outbound messages have
unique IDs and that status is tracked through webhooks, including `sent`,
`delivered`, `read`, and `failed` states. The implementation therefore treats
WhatsApp webhook evidence as authoritative for an already-known message ID.

## Remaining production gate

The implementation is test-verified. Real Meta webhook delivery, HTTPS endpoint
configuration, application-secret validation, Supabase RLS, and crash/replay
behavior still require controlled staging validation.
