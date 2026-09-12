# Phase 40 — Durable Business Automation Event Inbox

## Objective

Move Business Automation from best-effort event subscription toward recoverable event processing without changing the platform's authority model.

## Processing model

1. Unified EventBus provides low-latency in-process delivery.
2. Persisted `events` records provide the durable source for recovery.
3. `event_deliveries` provides an independent consumer-group delivery record.
4. `claim_business_automation_event_deliveries()` uses `FOR UPDATE SKIP LOCKED` and a lease so multiple bridge instances can safely compete.
5. The Business Automation bridge evaluates explicitly subscribed rules only.
6. Existing rule execution idempotency and governance remain authoritative.
7. Successful processing acknowledges the delivery; failed processing leaves it retryable.
8. A stale claim becomes reclaimable after the configured lease.

## Failure semantics

This is **at-least-once processing**, not exactly-once execution. Duplicate delivery is expected to be possible after crashes, so downstream action idempotency remains mandatory.

An event with invalid durable envelope data is negatively acknowledged and remains visible for operational investigation/retry rather than being silently discarded.

## Security

The new SQL functions are `SECURITY DEFINER`, use `search_path = public`, and are executable only by `service_role`. Client roles cannot directly claim or complete the inbox.

Workspace identity comes from the persisted event envelope. Rule parameters do not override it.

## Operational requirement

Migration `040_business_automation_event_inbox.sql` must be applied to the staging Supabase/Postgres database before durable recovery is considered operationally certified. Static tests cannot prove PostgreSQL locking, RLS, RPC privileges, or crash recovery.

## Scope boundary

This phase deliberately hardens the event-driven automation plane only. It does **not** imply that the rest of KDO is finished. Remaining platform work includes production dependency closure, external-provider reconciliation, real staging verification, security-route remediation, AI gateway consistency, observability/SLO validation, and broader feature-engine audits.
