# Phase 6 — Gate 3 Live Staging Certification Runbook

This is the operator runbook for closing Gate 3 against the real staging environment.

## Gate rule

Do not mark Gate 3 complete from local/synthetic evidence. Promotion requires authoritative machine-readable evidence from staging for every required probe.

## 0. Preconditions

Run these from a controlled staging operator environment, not from the isolated engineering sandbox:

- Python dependencies installed from the repository requirements.
- Network access to the KDO staging API and Supabase/PostgreSQL.
- A staging workspace and a second staging workspace for tenant-fence proof.
- A dedicated, isolated PostgreSQL restore target for backup certification.
- Redis staging endpoint available to the organism probe harness.
- A safe provider account/resource for controlled publication.
- Webhook delivery path available for idempotency/reconciliation verification.

Never paste secrets into source files, commits, tickets, or chat transcripts.

## 1. PostgreSQL execution fencing / tenant isolation

Required environment:

```bash
export SUPABASE_URL='https://<staging-project>.supabase.co'
export SUPABASE_SERVICE_ROLE_KEY='<staging-service-role-key>'
export KANYOZA_STAGING_WORKSPACE_ID='<workspace-a>'
export KANYOZA_STAGING_SECOND_WORKSPACE_ID='<workspace-b>'
```

Run:

```bash
python scripts/staging_production_certification.py \
  --live \
  --workspace-id "$KANYOZA_STAGING_WORKSPACE_ID" \
  --second-workspace-id "$KANYOZA_STAGING_SECOND_WORKSPACE_ID" \
  --wait-seconds 31
```

Required proof includes single-owner claim, lease-token enforcement, stale-token rejection, cross-workspace finalization rejection, and expired-lease reclaimability.

## 2. Runtime/readiness

Required environment:

```bash
export KANYOZA_STAGING_BASE_URL='https://<kdo-staging-host>'
export KANYOZA_STAGING_TOKEN='<staging-auth-token>'
export KANYOZA_STAGING_WORKSPACE_ID='<workspace-a>'
export KANYOZA_STAGING_BRAND_ID='<staging-brand>'
export KANYOZA_STAGING_VERIFY_URL='https://<kdo-staging-host>/<authoritative-verification-endpoint>'
```

Optional KCPG probe:

```bash
export KCPG_STAGING_BASE_URL='https://<kcpg-staging-host>'
export KCPG_STAGING_TOKEN='<kcpg-token>'
```

Run preflight first:

```bash
python scripts/staging_runtime_certification.py --live
```

A successful HTTP response is not sufficient for publication proof. The verification endpoint must expose explicit terminal execution state plus a provider/external object identifier.

## 3. Controlled provider publication

Only after preflight is promotable and the staging provider target is confirmed safe:

```bash
python scripts/staging_runtime_certification.py \
  --live \
  --publish \
  --prefix 'KDO-GATE3' \
  --wait-seconds 31
```

Required result:

- controlled action reaches a terminal successful state;
- canonical execution identity is preserved;
- provider/external object ID is present;
- authoritative verification observes that receipt;
- no duplicate side effect is created by polling/retry.

If publication succeeds externally but local verification cannot establish the receipt, classify it as **uncertain**, not failed, and reconcile before any retry.

## 4. Required staging organism probes

The staging validation harness requires authoritative results for:

- `database_rls`
- `redis`
- `execution_fencing`
- `webhook_idempotency`
- `provider_adapter`
- `organism_lifecycle`

Run the repository's staging probe harness with real adapters wired to staging. Missing or unobservable probes are blockers by design.

## 5. PostgreSQL backup / isolated restore

Required environment:

```bash
export DATABASE_URL='<source-staging-postgres-dsn>'
export KDO_RESTORE_DSN='<isolated-empty-restore-postgres-dsn>'
export KDO_BACKUP_VERIFY_QUERY='SELECT ... deterministic verification ...'
```

The restore target must be isolated from the source and must not be a production/staging database that contains unrelated live data.

Run:

```bash
python scripts/backup_restore_certification.py \
  --live \
  --source-dsn "$DATABASE_URL" \
  --restore-dsn "$KDO_RESTORE_DSN" \
  --verification-query "$KDO_BACKUP_VERIFY_QUERY"
```

Required proof includes backup creation, checksum/integrity verification, restore completion, and deterministic verification equality between source and restored data.

## 6. Evidence handling

Capture the JSON stdout from each certification runner as immutable evidence artifacts. Preserve:

- timestamp;
- staging environment identifier;
- correlation/intent/execution IDs;
- probe names and statuses;
- provider object IDs where applicable;
- backup checksum and restore verification;
- evidence digest produced by the repository evidence layer.

Do not edit a successful evidence artifact after capture. Re-run the probe if the evidence needs correction.

## 7. Gate decision

Gate 3 is **CLOSED / PASS** only when all required evidence is authoritative and present:

1. PostgreSQL/RLS + execution fencing — PASS
2. Redis lease/recovery — PASS
3. Controlled provider execution + external receipt — PASS
4. Webhook idempotency/reconciliation — PASS
5. Organism lifecycle/runtime — PASS
6. Isolated PostgreSQL backup/restore — PASS

Any `FAIL` or `UNOBSERVABLE` result keeps the gate closed.

## Current engineering status

The isolated repository currently has **37/37 local Gate-3 verification tests passing**. Real staging certification remains **NOT PROVEN** until the above live evidence is collected.
