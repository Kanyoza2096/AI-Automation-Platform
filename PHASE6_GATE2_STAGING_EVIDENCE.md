# KDO Phase 6 — Gate 2: Staging Evidence Certification

## Status

**LOCAL EVIDENCE CONTRACT: PASS**

**LIVE STAGING CERTIFICATION: NOT YET PROVEN**

This gate verifies that KDO can collect, authenticate, and fail-closed on staging evidence without converting missing external evidence into a production claim.

## Local verification

- Phase 6 Gate 2 evidence-contract tests: PASS
- Staging validation harness: PASS
- Evidence digest/tamper detection: PASS
- Missing required probes: BLOCK promotion
- Live staging runner without `--live`: BLOCKED
- Backup/restore runner without live infrastructure: `not_proven`
- Phase 6 Gate 1 regression suite: 32/32 PASS

## Required live evidence

The following must be executed against an isolated staging environment before the production gate can be promoted:

1. PostgreSQL RLS / tenant isolation and concurrent execution fencing.
2. Redis lease/recovery behavior.
3. Webhook idempotency and replay behavior.
4. A controlled provider adapter test with no unintended business mutation.
5. Organism lifecycle/startup/readiness behavior.
6. Backup to an isolated artifact and restore into a separate PostgreSQL target, followed by a deterministic verification query.

## Fail-closed rule

No local test, static inspection, or synthetic probe may be interpreted as proof of a live dependency. Missing, skipped, or unobservable evidence remains a blocker.

## Live commands

Run from the production/staging environment with the appropriate secret manager providing credentials:

```bash
python scripts/staging_production_certification.py --live \
  --workspace-id "$KANYOZA_STAGING_WORKSPACE_ID" \
  --second-workspace-id "$KANYOZA_STAGING_SECOND_WORKSPACE_ID"
```

Then run the real backup/restore certification with an isolated restore database:

```bash
python scripts/backup_restore_certification.py --live \
  --source-dsn "$DATABASE_URL" \
  --restore-dsn "$KDO_RESTORE_DSN" \
  --verification-query "$KDO_BACKUP_VERIFY_QUERY" \
  --evidence release/c9_3_backup_restore_evidence.json
```

Do not place credentials in committed evidence files.
