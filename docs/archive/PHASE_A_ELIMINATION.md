# Phase A — Elimination (auth / tokens / audit / workspace authority)

## Auth
- **Canonical:** `core.species_auth.SpeciesAuth`
- **Adapter only:** `core.api_auth.APIAuth` → delegates validate/generate to SpeciesAuth + APITokenManager
- Legacy `api_keys` validation: **fail-closed** unless `ALLOW_LEGACY_API_KEYS=1`

## Tokens
- **Canonical:** `core.api_tokens.APITokenManager` → table `api_tokens`
- `APIAuth.generate_key` creates via APITokenManager only
- `api_keys` table is not written by the adapter path

## Audit
- **Canonical:** `core.audit_service.AuditService` → table `audit_logs`
- `core.auth.AuditLogger` is a compatibility alias
- Registered in `dependency_registry` as both `AuditService` and `AuditLogger`

## Workspace authority
- Plugin execute paths use `run_plugin` / `resolve_plugin_workspace`
- Client `body.workspace_id` is not authoritative for ordinary principals

## Verify
```bash
# No active business writes to api_keys without opt-in
grep -rn 'table("api_keys")' --include='*.py' | grep -v ALLOW_LEGACY

# Auth entry points
grep -rn 'species_auth.validate\|APIAuth' --include='*.py' core/ api/
```
