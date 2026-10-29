# Phase 51 — Unified Publishing Contract

## Objective

Make the canonical publishing layer provider-aware without allowing each connector to silently reinterpret requests.

## Implemented

- Added `domain/publishing/provider_contract.py`.
- Centralized provider capability declarations for Facebook, Instagram, X/Twitter, LinkedIn, Telegram and WhatsApp.
- Canonical pipeline validates the request before connector selection/external side effects.
- Unsupported media is rejected deterministically instead of being silently discarded.
- Instagram requires a public image URL.
- WhatsApp requires a recipient.
- X/Twitter supports raw image bytes and threads; an image URL alone is rejected rather than degraded to text-only.
- Provider content limits are enforced where the provider contract does not support threading.
- Recipient fields are propagated through the canonical connector execution path.

## Contract semantics

`ConnectorResult.outcome` remains authoritative for provider ambiguity:

- `failed` — deterministic rejection/configuration/input error.
- `uncertain` — transport/provider ambiguity where the external side effect may have happened.
- `succeeded` — confirmed provider acceptance with a provider receipt.

The canonical pipeline continues to use the execution ledger and durable outbox as the application-side authority. Provider capability metadata does **not** claim native idempotency where the provider does not guarantee it.

## Verification

Full suite: **313 passed, 1 existing warning**.

The warning is the pre-existing `plugins.council` deprecation warning.

## Production status

This phase is code/test verified. It does not constitute external-provider production certification. Real OAuth credentials, provider permissions, Supabase/RLS, distributed outbox recovery and provider reconciliation still require controlled staging evidence.
