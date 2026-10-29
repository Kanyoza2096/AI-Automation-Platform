# Phase 48 — Cross-Platform Publishing Contract Hardening

Date: 2026-09-11
Status: code/test verified; external staging still required

## Scope

This phase audited the non-Facebook publishing path, with emphasis on LinkedIn and X/Twitter, against the canonical publishing spine.

## Defects corrected

1. **LinkedIn API drift** — the connector was still using the legacy `/v2/ugcPosts` API. It now targets the current `/rest/posts` Posts API and sends a versioned `Linkedin-Version` header. The version is configurable per social account and defaults to `202608`.
2. **LinkedIn media ambiguity** — image inputs were previously accepted but silently ignored. The connector now rejects media requests deterministically until a provider media-upload flow is supplied. This prevents false claims that an image was published.
3. **X/Twitter partial-thread success bug** — a thread whose first tweet succeeded but a later reply failed was previously returned as successful. It is now explicitly `uncertain` with reconciliation required, preventing automatic duplicate-thread retries.
4. **X/Twitter media-loss bug** — an `image_url` could be silently ignored when raw bytes were absent. The plugin now fails deterministically instead.
5. **Tenant/account convergence** — canonical publishing now prefers a deterministic `brand_id + platform` SocialAccount resolution when available, while retaining legacy plugin fallback for compatibility. In production, database/account-resolution failure blocks external publishing instead of silently selecting an unrelated/default account.

## Contract

Manual, scheduled, autonomous, and outbox execution continue to converge on the canonical pipeline. Provider-specific adapters must return explicit outcomes: `ok`, deterministic `failed`, or ambiguous `uncertain`. Ambiguous outcomes cannot be auto-retried unless authoritative reconciliation or native idempotency proves the side effect did not occur.

## Verification

- Full test suite: **300 passed, 1 warning**.
- Added provider regression coverage for LinkedIn API/version headers, LinkedIn media semantics, X/Twitter partial threads, X/Twitter media semantics, and deterministic brand-bound account selection.

## Remaining staging gates

- Real LinkedIn OAuth/account permissions and current API version acceptance.
- Real X/Twitter OAuth/media permissions and rate-limit behavior.
- Provider-side reconciliation against actual published object IDs.
- LinkedIn image/video upload path before enabling rich-media LinkedIn publishing.
- Real Supabase RLS/service-role behavior and distributed outbox lease recovery.

This phase does **not** certify external provider publication or production readiness.
