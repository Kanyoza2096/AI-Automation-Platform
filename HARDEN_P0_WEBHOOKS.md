# HARDEN P0 — Webhooks (architecture report §30)

## core/webhook_security.py
- `verify_github_signature` / `verify_facebook_app_secret`
- Production + missing secret → **reject** (not accept-all)
- Dev without secret → allow with warning
- `WebhookIdempotencyStore` — delivery/mid replay window

## GitHub `/api/v1/github/webhook`
- Requires `X-Hub-Signature-256`
- Dedup via `X-GitHub-Delivery`
- Reads raw body for HMAC

## Facebook `/webhook`
- Fail-closed via webhook_security when APP_SECRET missing in prod
- Parse JSON from body bytes (fixes body-consumed bug)
- Idempotency on message `mid`

## Env
- `GITHUB_WEBHOOK_SECRET` (required in production)
- `APP_SECRET` / `FACEBOOK_APP_SECRET` (required in production)
- `ENVIRONMENT=production|development`
