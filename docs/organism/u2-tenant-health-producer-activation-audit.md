# U2 tenant Facebook health producer activation audit

**Status:** design/implementation boundary audit  
**Scope:** repository source and migrations on the U2 stacked development branches  
**Safety:** read-only source inspection only; no database, provider, deployment, or production operations

## Findings

### 1. The organism event-consumption side is already wired

The root lifecycle.py boot path resolves and starts the canonical UnifiedEventBus, then creates OrganismEventBridge with the authenticated-workspace OrganismRegistry and subscribes it to the bus wildcard. The bridge only enriches an organism that already exists for the event workspace; it does not create tenant organisms.

organism/event_bridge.py maps only an allowlist of event types, bounds copied payload fields, and leaves connector-health observations untrusted unless the exact Facebook producer identity, workspace, brand, account, connection, and evidence lineage are present. This side is not the current blocker.

### 2. The health producer is a contract, not yet an application call path

services/v69_tenant_facebook_health_producer.py requires the caller to supply:
- an authenticated identity and brand ID;
- a workspace-resolved IntegrationConnection;
- a trusted ConnectorContract;
- a matching ConnectorRegistryRecord;
- a durable SupabaseIntegrationEvidenceStore.

The producer has no internal production caller or API route in the inspected source. routes/connector_fabric_routes.py exposes read-only GET surfaces only. The current TenantFacebookHealthProducer implementation and tests therefore prove a bounded component contract, not a live operational health-monitoring workflow.

### 3. Resource identity is not yet persisted in the integration control-plane rows

Migration 066 defines integration_connections with workspace, connector, state, capabilities, autonomy mode and JSON metadata. It does not define dedicated brand_id or social_account_id columns.

Migration 068 defines connector_certification_registry with workspace, connection, connector/version, certification and registry state. It also has no dedicated brand/account columns.

By contrast, PR #98's in-memory IntegrationConnection and ConnectorRegistryRecord contracts carry optional brand_id and social_account_id. TenantFacebookConnectorFactory requires these identities to match the resolved TenantSocialAccountBinding exactly.

The TenantSocialAccountResolver can independently verify that a requested brand and Facebook Page account belong to the authenticated workspace. But the current integration connection/registry persistence contract does not provide a first-class, constrained binding to that same brand/account. Relying on arbitrary caller-created dataclasses would weaken the trust boundary.

### 4. Durable health evidence is available only after its migrations

SupabaseIntegrationEvidenceStore.record_health_observation() calls the atomic record_connector_health_observation RPC. Migration 161 writes the health observation and evidence record in one transaction; migration 160 provides the append-only hash-chain writer; migration 159 adds tenant/connection foreign-key hardening.

PR #102 now keeps the producer unavailable unless:
- an operator explicitly sets KDO_TENANT_FACEBOOK_HEALTH_EVIDENCE_READY=true, attesting that migrations 159–161 are applied and verified in the intended environment; and
- read-only, zero-row schema probes confirm the required integration connection, evidence, and health-observation columns.

That flag has not been enabled. No migrations have been applied by this work.

### 5. Production-grade execution dependencies are not yet composed

ExecutionLedger requires an injected durable database in production; its in-memory mode is for tests/local operation. The inspected dependency registry does not currently register the tenant Facebook health producer, tenant resolver, connector factory, or a durable execution-ledger instance as a complete service.

The global Facebook service object exists, but the account-bound connector must continue to use the resolved Page account credentials and must not fall back to global Page credentials.

## Required next implementation slices

1. **Persist the resource binding:** design a migration that stores brand and social-account identity on the workspace-owned integration connection and certification registry, with constraints ensuring the registry binding matches its connection. Keep legacy rows nullable and fail closed for Facebook health until they have an explicit binding. Before adding foreign keys to brands/social accounts, verify their actual deployed key/unique constraints rather than assuming them.
2. **Build a trusted context resolver:** accept only authenticated workspace/brand/connection identifiers; load the connection and certification registry through an explicitly injected service-role client; resolve the Facebook Page through TenantSocialAccountResolver; require every workspace, brand, account, connector, version, certification and registry field to agree.
3. **Compose a health-observation service:** inject the canonical bus, the gated producer capability, the tenant context resolver, the account-bound connector factory, a durable ExecutionLedger, and SupabaseIntegrationEvidenceStore. Do not accept caller-constructed connection/registry objects from an API request.
4. **Expose an explicit, authenticated observe-only operation:** add a route or governed scheduled job only after the trusted context resolver exists. It must be read-only toward Facebook, persist evidence before publishing, and leave suggestions at suggest/proposed; it must not activate, repair, publish, or mutate a connector.
5. **Verify in staging only after explicit approval:** review and approve the required migrations, provision producer keys in staging secrets, enable the readiness attestation, and run one controlled test with a test workspace/account. Keep production database and production service untouched.

## Explicitly not done

- No production or staging database was queried or mutated.
- No migration was applied.
- No Facebook/Meta API request was made.
- No producer key was generated or provisioned.
- No event was published by the new producer.
- No Render deployment, branch promotion, merge to staging, or merge to main occurred.

This audit deliberately separates a verified event/goal proposal architecture from a real producer activation. The latter must wait for persisted tenant resource binding and a trusted runtime context resolver.
