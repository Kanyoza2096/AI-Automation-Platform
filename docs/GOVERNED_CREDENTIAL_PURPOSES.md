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

## Implemented in this change

- Token purpose and resource constraints are carried by AuthIdentity and
  immutable RequestContext.
- Canonical token authentication rejects non-default and unknown purposes until
  their endpoint consumers are explicitly enabled.
- Website integration registration requires a human Supabase-JWT workspace
  administrator or human platform super-admin. Machine API tokens and species
  service credentials cannot register integrations, even if a token has an
  admin permission string.
- Regression tests cover rejection of connector/unknown purposes and request
  context propagation.

## Remaining before API Manager selection

1. Add endpoint-specific purpose/resource guards for each service consumer.
2. Define server-owned permission presets for service integrations.
3. Expose only purposes whose consumers are fully enforced.
4. Add cross-workspace, resource mismatch, expiry, revocation, and privilege
   escalation tests at the route/service boundary.
