# Phase 57 — Governed Affiliate Workload

## Purpose

Affiliate marketing is implemented as a workload on KDO's existing governed action architecture. It does **not** introduce a second action registry, execution ledger, or autonomous side-effect plane.

The invariant remains:

> Affiliate automation may propose or enqueue work, but external side effects must cross the Action Kernel and existing publishing pipeline.

## Included

- Durable affiliate product registry.
- Durable tracking-link registry with globally unique public slugs.
- Privacy-minimized click events and daily click rollups.
- Conversion events with idempotent provider/order identity and lifecycle states (`pending`, `approved`, `reversed`, `paid`).
- Daily revenue/commission rollups.
- Signed conversion webhooks.
- Approved-domain HTTPS redirect validation to prevent the tracking endpoint becoming an arbitrary open redirect.
- Policy-driven affiliate disclosure injection.
- Action Kernel contracts:
  - `affiliate_create_product`
  - `affiliate_create_link`
  - `affiliate_publish_promotion`
  - `affiliate_pause_product`
  - `affiliate_optimize_campaign`
- Business Automation adapters that enqueue affiliate publication or request governed optimization.

## Safety decisions

1. Workspace identity is taken from authenticated request context for operator APIs. A caller cannot select an arbitrary workspace ID.
2. Public conversion webhooks authenticate with an HMAC secret and derive workspace ownership from the authenticated product record rather than trusting a webhook-supplied workspace ID.
3. Affiliate publishing is high-risk and approval-gated. It reuses the canonical publishing pipeline.
4. Pausing an affiliate product is high-risk and approval-gated.
5. Optimization is advisory. It can produce evidence/recommendations but cannot silently pause or mutate revenue-producing assets.
6. Destination URLs must be HTTPS, cannot contain credentials or non-standard ports, and must match an approved product domain.
7. Disclosure is policy-driven; the implementation does not claim regulatory approval and does not hard-code a jurisdiction-specific legal conclusion.

## Routes

Public:

- `GET /api/v1/affiliate/go/{slug}` — validate, record click, then 307 redirect.
- `POST /api/v1/affiliate/webhooks/{provider}` — signed conversion ingestion.

Authenticated/admin:

- `POST /api/v1/affiliate/products`
- `POST /api/v1/affiliate/links`
- `GET /api/v1/affiliate/analytics`
- `POST /api/v1/affiliate/actions`

## Evidence

Phase 57 tests cover redirect policy, disclosure idempotence, webhook signatures, Action Kernel contract registration, and approval gating.

Current verification after this phase:

- Full suite: **336 passed, 1 warning**.
- Migration structure: **49 SQL files**, structural warnings only for historical duplicate/gapped prefixes.
- Security surface: **11/11 checks passed**.
- Production certification static gate: **PASS**.

The static gate is not evidence of live affiliate-provider correctness. Staging evidence is still required for real provider webhooks, database/RLS behavior, and external publishing/reconciliation.
