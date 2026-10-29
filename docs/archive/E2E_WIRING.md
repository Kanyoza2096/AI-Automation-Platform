# End-to-end wiring (hands are real)

## What was fixed

1. **Director** — `services/director_bindings.py` replaces `_placeholder` with real handlers.
2. **Publish path** — `services/publish_orchestration.py`: metabolism → decision → job → Gemini/caption → plugins → bus → quota.
3. **Social plugins** — `ensure_social_plugins` on boot (Facebook always attempted; Twitter/LinkedIn if tokens).
4. **Condition engine** — real executor for content boost/reduce/safe_mode.
5. **Dreams** — linked to organism council when available.

## Test safely

```bash
# dry_run (no Graph API post)
curl -X POST "$HOST/api/v1/e2e/publish" \
  -H "Authorization: Bearer $MASTER_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"workspace_id":"default","topic":"Hello from Kanyoza","dry_run":true}'

# Live post (needs Facebook page token on brand/account)
curl -X POST "$HOST/api/v1/e2e/publish" \
  -H "Authorization: Bearer $MASTER_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"workspace_id":"default","brand_id":"YOUR_BRAND","dry_run":false,"platform":"facebook"}'

# Director hands status
curl "$HOST/api/v1/e2e/director-hands" -H "Authorization: Bearer $MASTER_API_TOKEN"
```

## Required secrets for full live path

- `GEMINI_KEY` — caption generation
- Facebook page token in social_accounts / vault
- Supabase (jobs + organism_state optional but recommended)

## Critical capabilities stay blocked

`delete_brand`, `drop_database`, `rotate_credentials`, etc. remain `_blocked` (species law).
