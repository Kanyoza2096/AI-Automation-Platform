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

Migration `167_governed_credential_purposes.sql` adds
`credential_purpose` and `resource_constraints` to `api_tokens`.
Existing records are backfilled by the database default to `workspace_api`.
The token hash remains the only stored secret representation.

## Security boundary

Purpose metadata is not a substitute for endpoint authorization. A consumer
must check both the purpose and its resource constraints before allowing a
connector-specific operation. The dedicated website event-ingress consumer is
now enabled; service-integration credentials remain disabled.

## Implemented in this change

- Token purpose and resource constraints are carried by AuthIdentity and
  immutable RequestContext.
- Generic workspace API authentication continues to reject website-connector
  and service-integration purposes.
- Website connector credentials can be issued only through the dedicated
  human-admin route after the integration is verified in the caller's workspace.
- Connector tokens receive a server-owned `write` permission preset, a
  90-day expiry, and exactly one `integration_id` resource constraint.
- Only the matching website event-ingress route accepts connector-purpose
  credentials; it rejects workspace tokens, service credentials, mismatched
  integration IDs, missing write permission, expired tokens, and revoked tokens.
- Connector credentials can be revoked only by a human workspace administrator
  and only within their owning workspace and integration.
- Website integration registration requires a human Supabase-JWT workspace
  administrator or human platform super-admin. Machine API tokens and species
  service credentials cannot register integrations, even if a token has an
  admin permission string.
- Regression tests cover rejection of connector/unknown purposes and request
  context propagation.

## Remaining

1. Add the matching governed credential workflow to the Control Plane UI.
2. Add full integration tests against the mounted FastAPI application and
   durable Supabase token storage.
3. Keep `service_integration` disabled until its own endpoint consumers,
   permission presets, and resource-policy tests are implemented.
