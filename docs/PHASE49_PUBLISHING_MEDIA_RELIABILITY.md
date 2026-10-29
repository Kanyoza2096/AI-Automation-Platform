# Phase 49 — Publishing Media Reliability

Date: 2026-09-12  
Status: code/test verified; external staging still required

## Scope

This phase continues the cross-platform publishing audit after Phase 48, focusing on provider media semantics and preventing silent degradation from media posts to text-only posts.

## Changes

### LinkedIn

- Added the current LinkedIn Images API upload flow for `image_bytes`.
- Initializes the provider asset, uploads the bytes, and polls the authoritative image resource until `AVAILABLE`.
- Publishes the resulting image URN through the current Posts API.
- Processing failures and provider/transport ambiguity are surfaced as deterministic `failed` or `uncertain` outcomes.
- `image_url` without an explicit media-ingestion step remains rejected rather than fetched implicitly.
- Existing per-account LinkedIn API versioning remains supported, defaulting to the current `202608` release.

LinkedIn documents the Images API as the replacement for the older Assets API and requires the versioned REST headers; image content is then referenced by its image URN in the Posts API.

### X/Twitter

- Media upload failure can no longer silently fall through to a text-only tweet.
- HTTP 4xx media failures are deterministic `failed` results.
- 5xx/transport/media-ID ambiguity becomes `uncertain`, preventing an unsafe automatic retry.
- Successful media upload remains required before the tweet is created.

## Reliability contract

For every provider:

1. Requested media must either be attached to the external post or the publish must fail.
2. A provider-side ambiguous media operation cannot be converted into a successful text-only operation.
3. Any externally visible partial side effect remains `uncertain` until reconciliation.
4. The canonical pipeline remains the authority for governance, idempotency, execution ledger, outbox, and reconciliation.

## Verification

- Full test suite: **304 passed, 1 warning**.
- Added regression coverage for LinkedIn image initialization/upload/availability and timeout behavior.
- Added regression coverage proving X/Twitter media-upload failure never degrades into text-only publication.

## Remaining staging gates

- Real LinkedIn organization/member permissions for Images + Posts APIs.
- Real X/Twitter media upload permissions and current API behavior.
- Real provider reconciliation using authoritative post IDs.
- Supabase RLS and distributed outbox lease/recovery validation.
- Production dependency/runtime certification.

This phase improves provider correctness but does **not** certify production readiness or external provider publication.
