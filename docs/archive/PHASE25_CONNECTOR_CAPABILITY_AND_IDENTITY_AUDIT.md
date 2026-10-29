# Phase 25 — Connector Capability, Identity & External Effect Audit

## Scope
Audited connector factory, Facebook, Instagram, LinkedIn, Twitter/X, Telegram,
WhatsApp, governed connector, execution ledger, canonical publish pipeline and
reconciliation boundary.

## Findings

### P1 — Idempotency identity was not destination-account scoped
A generated publish idempotency key previously used workspace/brand/platform/content
but not the destination social account. Multiple accounts of the same platform in one
workspace could therefore collide and replay an execution belonging to another account.

### P1 — Media payload was not part of the canonical identity
Identical text with different binary media could share an execution identity. The key
now incorporates a SHA-256 digest of image bytes.

## Fix
Added `core/execution_identity.py` as the canonical publish identity helper and updated
the canonical publishing pipeline to include the current authenticated destination
social-account context.

## Capability truth
Connector capability declarations correctly distinguish native idempotency from KDO's
application-level ledger. Providers without safe ambiguous retry are not marked safe.
Unsupported edit/delete operations return explicit failures rather than success.

## Important staging verification still required
Provider API semantics, credentials, permission scopes, rate limits, media rules and
real reconciliation behavior must still be tested against staging/sandbox accounts.
