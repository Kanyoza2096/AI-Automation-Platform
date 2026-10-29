# Phase 50 — Instagram / Telegram / WhatsApp Publishing Reliability

## Scope

Harden the remaining messaging/social connectors so media is never silently discarded and provider ambiguity is preserved for the durable execution/reconciliation layer.

## Changes

- Instagram image containers are polled until `FINISHED`/`PUBLISHED`; provider rejection fails deterministically and processing timeout is `uncertain`.
- Telegram now supports public `image_url` media publishing in addition to raw bytes.
- Telegram transport/timeouts are classified as `uncertain`; provider 4xx failures are deterministic `failed`.
- WhatsApp now publishes image URLs as image messages instead of silently downgrading to text.
- WhatsApp raw bytes without a public URL fail closed rather than being discarded.
- WhatsApp outbound message IDs remain webhook-reconciled; `lookup_post()` therefore returns `None` instead of falsely claiming absence.

## Provider contract basis

Telegram Bot API supports `sendPhoto` with either an existing file ID, an HTTP URL, or multipart upload. WhatsApp Cloud API sends media through `/messages` and returns message IDs that can be tracked by webhooks. Instagram publishing uses a media-container flow before publication.

## Verification

308 tests passed, 1 existing Council deprecation warning.

This is code/test verification only. Real provider credentials, external API calls, webhook delivery, Supabase RLS, and distributed outbox recovery remain staging certification gates.
