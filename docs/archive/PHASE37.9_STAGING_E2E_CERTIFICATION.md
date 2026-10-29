# Phase 37.9 — Real Staging Publish Certification

This phase provides a controlled runner for proving the real manual, scheduled,
and automated Facebook publish paths. It does not confuse API acceptance with
provider publication.

## Runner

```bash
python scripts/staging_e2e_publish.py
python scripts/staging_e2e_publish.py --live
```

The first command is intentionally blocked. Real mutations require `--live`.

Required staging environment variables:

- `KANYOZA_STAGING_BASE_URL`
- `KANYOZA_STAGING_TOKEN`
- `KANYOZA_STAGING_WORKSPACE_ID`
- `KANYOZA_STAGING_BRAND_ID`

Optional:

- `KANYOZA_STAGING_VERIFY_URL` — an authenticated staging verification endpoint
  that reports durable execution/provider results.
- `--wait-seconds N` — wait before calling the verification endpoint.
- `--prefix TEXT` — prefix for certification captions.

## Certification sequence

1. Immediate/manual `POST /api/v1/posts`.
2. Future scheduled `POST /api/v1/posts`.
3. Automated `trend.publish.requested` through `POST /api/events/publish`.
4. Verify durable execution state and Facebook provider object IDs.
5. Verify final job/scheduled state and learning feedback.

A `202` or `queued` response proves only acceptance. A successful certification
requires authoritative durable/provider evidence showing the Facebook post ID
and terminal execution state.

## Safety boundaries

- Workspace and brand are mandatory; no platform/default tenant fallback.
- The runner never embeds or prints credentials.
- The runner never treats an unavailable verification endpoint as publication.
- Real mutations require explicit `--live`.
- Existing Guardian, DecisionGate, execution ledger, outbox and connector
  authority remains unchanged.
