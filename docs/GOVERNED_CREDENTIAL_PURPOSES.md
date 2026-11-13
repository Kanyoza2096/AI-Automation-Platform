# Governed machine credential purposes

## Purpose model

Machine API credentials have an explicit purpose, independent of the human
platform authority model:

- `workspace_api`: ordinary workspace API access (legacy default).
- `service_integration`: service-to-service access, scoped to a workspace.
- `website_connector`: website connector access, scoped to a workspace and
  constrained to declared website integration resources.

Human platform super-admin authority is never minted as an API token. It
continues to come only from the verified human identity exchange.

## Persistence

Migration `052_governed_credential_purposes.sql` adds
`credential_purpose` and `resource_constraints` to `api_tokens`.
Existing records are backfilled by the database default to `workspace_api`.
The token hash remains the only stored secret representation.

## Security boundary

Purpose metadata is not a substitute for endpoint authorization. A consumer
must check both the purpose and its resource constraints before allowing a
connector-specific operation. Until each consumer is updated to enforce those
checks, issue only `workspace_api` credentials. Do not issue a website or
service credential for production use yet.

## Planned follow-up

1. Propagate purpose and constraints through SpeciesAuth and RequestContext.
2. Add endpoint-specific purpose/resource guards.
3. Expose purpose selection in API Manager with server-owned scope presets.
4. Add tests for cross-workspace access, resource mismatch, expiry, revocation,
   unknown purpose, and attempted platform-authority escalation.
