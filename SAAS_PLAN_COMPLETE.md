# Kanyoza SaaS organism plan — COMPLETE (backend)

Bio architecture preserved. Species law → closed nerve → multi-organism →
metabolism → visible body → scalable species.

## Apply migrations in order

- `013_species_law.sql`
- `014_metabolism.sql`
- `015_organism_state.sql`

## Critical env

```
MASTER_API_TOKEN=
ENCRYPTION_KEY=   # Fernet
ENVIRONMENT=production
SERVICE_MODE=full   # or api / core / workers
# Stripe optional
STRIPE_SECRET_KEY=
```

## Never again

- APP_SECRET / PSID / `?token=` as API auth
- Chaos in production
- Publish without `decision_id` (strict mode)
- Trusting `X-Workspace-ID` alone for tenants
