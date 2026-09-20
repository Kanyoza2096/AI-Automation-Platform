# Phase 43 — Native Website Lead Capture

## Objective

Move KDO one step beyond generic automation plumbing by giving a customer website a typed, first-party lead intake contract.

## Runtime path

`Website form → KDO lead-capture endpoint → source-secret authentication → durable receipt fence → UnifiedEventBus → BusinessAutomationEventBridge → explicit lead.created rule → Action Kernel → CRM contact`

The HTTP ingress never writes the CRM directly and never executes an automation inline. Consequential work remains governed by the existing Action Kernel.

## Endpoints

- `POST /api/v1/business-automation/lead-capture/{source_id}` — typed website lead adapter.
- `POST /api/v1/business-automation/event-sources/{source_id}/ingest` — generic machine event adapter.

Both are on a separate public ingress router because customer machine callers do not have KDO user sessions. They authenticate with the one-time-issued source secret and are rate-limited at the IP layer.

## Website lead contract

Accepted fields: `name` (required), `phone`, `email`, `company`, `message`, `source`, `page_url`, and optional `event_id`.

The adapter emits exactly `lead.created`; customers do not control the event type on this typed endpoint. This prevents a website form from impersonating unrelated business events.

## Provider

`website_lead` is now a supported event-source provider. Its event allowlist is fixed to `lead.created`.

## Security / reliability

- source secrets are stored as SHA-256 digests and returned only at creation;
- event IDs are replay-fenced through the durable receipt RPC;
- duplicate published events are acknowledged without re-publication;
- publish failures retain an unpublished receipt so the same event ID can be retried;
- workspace identity comes from the event source record, never from the website payload;
- the ingress boundary does not call CRM or other external side-effecting services.

## Remaining production proof

This phase proves the contract and wiring offline. It does **not** claim a live website/CRM provider E2E certification until deployed staging credentials and a real customer website submit are exercised.
