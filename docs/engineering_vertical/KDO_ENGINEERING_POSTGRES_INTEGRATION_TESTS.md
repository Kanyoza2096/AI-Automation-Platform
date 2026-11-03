# Real PostgreSQL Queue Integration Tests

The unit suite for Phase 16.4 uses fake DB-API connections to validate SQL
construction and fail-closed behavior. The opt-in integration suite
(`test_phase16_4_postgres_integration.py`) exercises the adapter against a
real PostgreSQL server.

## Safety requirements

- Use a disposable, dedicated integration database. Never point this test at
  production or a shared staging database.
- Apply `migrations/140_kdo_engineering_shared_build_queue.sql` through the
  normal migration process before running the tests.
- The connection must use a server-side role permitted to access the queue
  table (for Supabase, the intended server-side `service_role` path).
- The suite creates uniquely scoped fixture rows and deletes only those exact
  tenant/workspace scopes. It never truncates or drops the queue table.
- Keep the DSN in a local environment variable or CI secret; never commit it.

## Run

Install the repository requirements, then set
`KDO_ENGINEERING_TEST_POSTGRES_DSN` to the disposable database DSN:

```bash
export KDO_ENGINEERING_TEST_POSTGRES_DSN='postgresql://...'
python -m pytest -q tests/engineering_vertical/test_phase16_4_postgres_integration.py
```

Without the variable, the four live-database tests are skipped. The focused
engineering GitHub Actions workflow provisions an ephemeral PostgreSQL 16
service, creates the expected non-login roles, applies the migration, and sets
the DSN for the test step. Thus the focused PR workflow runs the real database
tests rather than treating skips as integration evidence.

## Coverage

- Concurrent workers claim distinct queued jobs using real row locks and
  `FOR UPDATE SKIP LOCKED`.
- A scoped worker cannot claim a job belonging to another tenant/workspace.
- Expired lease tokens cannot renew or complete jobs.
- Confirmed-dead workers with expired leases are reconciled to `uncertain`,
  not silently requeued or rerun.

These tests do not activate the worker in production, authorize execution,
approve patches, or grant release authority.
