# Phase A — Species Law — COMPLETE (code)

Goal: lock the doors so the organism can be sold without cross-tenant infection.

## Checklist

| Item | Status | Location |
|------|--------|----------|
| Single auth (`SpeciesAuth`) | Done | `core/species_auth.py` |
| `require_auth` uses SpeciesAuth only | Done | `api/v1.py` |
| Reject APP_SECRET as API key | Done | `species_auth.py` |
| Reject owner PSID as API key | Done | `species_auth.py` |
| Reject `?token=` query credentials | Done | `require_auth` + `extract_token_from_request` |
| Workspace from token, not header | Done | `species_auth` + `workspace_context` |
| Async-safe contextvars | Done | `core/workspace_context.py` |
| Nerve envelope (`workspace_id`, `correlation_id`) | Done | `core/nerve.py` + `unified_bus.publish` |
| Reflexes marked SPECIES_LEVEL | Done | `core/reflexes.py` |
| Command executor owner-gated | Done | `services/command_executor.py` |
| `decision_id` on AutomationJob | Done | `models/automation_job.py` |
| RLS + revoke anon grants | Done | `migrations/013_species_law.sql` |
| Credential vault production fail-closed | Already present | `core/credential_vault.py` |

## Operator actions (your part)

1. Apply `migrations/013_species_law.sql` in Supabase.
2. Ensure `MASTER_API_TOKEN` is set and rotated if it was ever committed.
3. Ensure `ENCRYPTION_KEY` is a valid Fernet key in production.
4. Stop using APP_SECRET / PSID / query tokens as API credentials in any clients.
5. Pass `caller_is_owner=True` into `CommandExecutor.execute` only after verifying `OWNER_PSID` on the webhook path.

## Next: Phase B

One nerve, one loop — subscribe matrix, strict `workspace_id` on every publish, no job without `decision_id`.
